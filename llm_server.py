#!/usr/bin/env python3
"""
Simple LLM HTTP server using FastAPI.
- Loads a local GGUF model once on startup (configurable via MODEL_PATH env var)
- POST /ask accepts JSON { prompt, system_prompt?, generation_params? } and returns { response }
- POST /shutdown optionally protected by LLM_SHUTDOWN_TOKEN env var (if set)
- Writes .llm_server_pid in the project dir for compatibility with existing scripts

Run: python3 llm_server.py
Or run via: uvicorn llm_server:app --host 127.0.0.1 --port 5005

This server intentionally does NOT keep conversation state between requests: each /ask call is independent.
"""

import html
import os
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any
import asyncio
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from llama_cpp import Llama
except Exception:
    Llama = None

APP_DIR = Path(__file__).resolve().parent
PID_FILE = APP_DIR / '.llm_server_pid'
DEFAULT_MODEL = os.environ.get('MODEL_PATH', '/home/asher/.lmstudio/models/lmstudio-community/gemma-3-1b-it-GGUF/gemma-3-1b-it-Q4_K_M.gguf')
SHUTDOWN_TOKEN = os.environ.get('LLM_SHUTDOWN_TOKEN')
# Default to 0.0.0.0 so the server is reachable from other machines on the LAN;
# override with LLM_HOST env var if you want to bind to a specific address.
HOST = os.environ.get('LLM_HOST', '0.0.0.0')
PORT = int(os.environ.get('LLM_PORT', '5005'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
app = FastAPI()

# Allow browser pages (the Pulse UI) to call the local LLM server directly.
# For local development this is permissive; for production set more restrictive origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AskRequest(BaseModel):
    prompt: str
    system_prompt: Optional[str] = None
    generation_params: Optional[Dict[str, Any]] = None

class AskResponse(BaseModel):
    response: str

def format_sse(data: str) -> str:
    """
    Format a string as one or more SSE 'data:' lines, preserving blank lines.
    Ensures output ends with the required double-newline separator.
    """
    if data is None:
        return 'data: \n\n'
    # Split on LF (preserves empty items for consecutive newlines)
    parts = data.split('\n')
    out_lines = []
    for p in parts:
        # For empty line produce an empty data: to preserve blank line
        out_lines.append(f"data: {p}")
    return '\n'.join(out_lines) + '\n\n'

@app.on_event('startup')
def startup_event():
    if Llama is None:
        logging.error('llama_cpp not available; cannot start LLM server.')
        return

    model_path = os.environ.get('MODEL_PATH', DEFAULT_MODEL)
    if not Path(model_path).exists():
        logging.error(f'Model path not found: {model_path}')
        return

    # Default Llama params (tuned for local 24/7 server; adjust via env or request)
    llama_params = {
        'model_path': model_path,
        'n_ctx': int(os.environ.get('LLM_N_CTX', '2048')),
        'n_threads': int(os.environ.get('LLM_N_THREADS', '4')),
        'n_gpu_layers': int(os.environ.get('LLM_N_GPU_LAYERS', '0')),
        'verbose': False
    }

    logging.info(f"Loading model from {model_path} ...")
    try:
        app.state.llm = Llama(**llama_params)
        logging.info('Model loaded successfully.')
    except Exception as e:
        logging.exception('Failed to load model: %s', e)
        app.state.llm = None

    # write pid file for external control (pulse.php may still expect a pid file)
    try:
        pid = os.getpid()
        PID_FILE.write_text(str(pid))
        logging.info(f'Wrote PID {pid} to {PID_FILE}')
    except Exception:
        logging.exception('Failed to write PID file')

@app.on_event('shutdown')
def shutdown_event():
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
            logging.info('Removed PID file.')
    except Exception:
        logging.exception('Error removing PID file on shutdown')

@app.get('/health')
def health():
    return {'status': 'ok', 'model_loaded': bool(getattr(app.state, 'llm', None))}


@app.get('/', response_class=HTMLResponse)
def index():
        # Simple test UI to submit prompts to /ask
        html = '''
        <!doctype html>
        <html>
        <head><meta charset="utf-8"><title>LLM Server</title></head>
        <body style="font-family: Arial, Helvetica, sans-serif; margin:20px;">
            <h2>LLM Server</h2>
            <form id="frm">
                <label>System prompt (optional)</label><br>
                <input id="system" style="width:100%" placeholder="System prompt"><br><br>
                <label>Prompt</label><br>
                <textarea id="prompt" rows="6" style="width:100%" placeholder="Enter your prompt"></textarea><br>
                <button type="submit">Ask</button>
            </form>
            <h3>Response</h3>
            <pre id="resp" style="white-space:pre-wrap; background:#f6f6f6; padding:12px; border-radius:6px; max-width:800px;"></pre>

            <hr>
            <h3>News Q&A</h3>
            <p style="margin-top:0;"><small>Ask a concise question about recent news items — response streams below.</small></p>
            <form id="news-qa">
                <textarea id="qa_prompt" rows="3" style="width:100%" placeholder="Ask about the latest news in this area..."></textarea><br>
                <button type="submit">Ask News</button>
            </form>
            <h4>Answer</h4>
            <pre id="qa_resp" style="white-space:pre-wrap; background:#fff9e6; padding:12px; border-radius:6px; max-width:800px;"></pre>

            <script>
                // Append a stream chunk but ensure words don't run together across chunk boundaries.
                function appendChunk(el, chunk) {
                  if (!chunk) return;
                  try {
                    // If existing text does not end with whitespace and chunk does not start with whitespace, add a space.
                    if (el.textContent && !(/\s$/.test(el.textContent)) && !(/^\s/.test(chunk))) {
                      el.textContent += ' ';
                    }
                  } catch (e) { /* ignore regex errors in exotic environments */ }
                  el.textContent += chunk;
                }
                document.getElementById('frm').addEventListener('submit', async function(e){
                     e.preventDefault();
                     const prompt = document.getElementById('prompt').value;
                     const system = document.getElementById('system').value || undefined;
                     const payload = { prompt: prompt };
                     if (system) payload.system_prompt = system;
 
                     const respEl = document.getElementById('resp');
                     respEl.textContent = '';
 
                     try {
                         const r = await fetch('/ask?stream=1', {
                             method: 'POST',
                             headers: {
                                 'Content-Type': 'application/json',
                                 'Accept': 'text/event-stream'
                             },
                             body: JSON.stringify(payload)
                         });
 
                         const ct = (r.headers.get('content-type') || '').toLowerCase();
                         if (!r.ok) {
                             const txt = await r.text();
                             respEl.textContent = 'Error: ' + r.status + '\\n' + txt;
                             return;
                         }
 
                         // If server returned JSON (no streaming), show it and return
                         if (ct.includes('application/json')) {
                             const j = await r.json();
                             respEl.textContent = JSON.stringify(j, null, 2);
                             return;
                         }
 
                         // Otherwise consume the body as a stream (SSE style framing expected)
                         const reader = r.body.getReader();
                         const dec = new TextDecoder();
                         let buf = '';
 
                         while (true) {
                             const { done, value } = await reader.read();
                             if (done) break;
                             buf += dec.decode(value, { stream: true });
                             // SSE events are separated by double-newline
                             const parts = buf.split('\\n\\n');
                             buf = parts.pop();
                             for (const part of parts) {
                                 // each part may contain lines like "data: ..." or "event: done"
                                 const lines = part.split('\\n');
                                 let dataLines = lines.filter(l => l.startsWith('data:'));
                                 if (dataLines.length) {
                                     const data = dataLines.map(l => l.slice(6)).join('\\n');
                                     if (data === '[DONE]') {
                                         // done marker; optionally stop
                                     } else {
                                        appendChunk(respEl, data);
                                     }
                                 } else {
                                     // fallback: append raw chunk
                                    appendChunk(respEl, part);
                                 }
                                 // keep UI scrolled
                                 respEl.scrollTop = respEl.scrollHeight;
                             }
                         }
 
                         // flush any remaining buffer
                        if (buf && buf.trim()) {
                            appendChunk(respEl, buf);
                        }
                     } catch (err) {
                         respEl.textContent = 'Request failed: ' + String(err);
                     }
                 });
 
                // News Q&A handler: sends the prompt with a concise news-analyst system prompt and streams into qa_resp
                document.getElementById('news-qa').addEventListener('submit', async function(e){
                     e.preventDefault();
                     const prompt = document.getElementById('qa_prompt').value;
                     if (!prompt || !prompt.trim()) return;
                     const payload = { prompt: prompt, system_prompt: 'You are a concise news analyst. Answer briefly and focus on recent relevant events.' };
 
                     const respEl = document.getElementById('qa_resp');
                     respEl.textContent = '';
                     try {
                         const r = await fetch('/ask?stream=1', {
                             method: 'POST',
                             headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                             body: JSON.stringify(payload)
                         });
                         if (!r.ok) {
                             const txt = await r.text();
                             respEl.textContent = 'Error: ' + r.status + '\\n' + txt;
                             return;
                         }
                         const reader = r.body.getReader();
                         const dec = new TextDecoder();
                         let buf = '';
                         while (true) {
                             const { done, value } = await reader.read();
                             if (done) break;
                             buf += dec.decode(value, { stream: true });
                             const parts = buf.split('\\n\\n');
                             buf = parts.pop();
                             for (const part of parts) {
                                 const lines = part.split('\\n');
                                 let dataLines = lines.filter(l => l.startsWith('data:'));
                                 if (dataLines.length) {
                                     const data = dataLines.map(l => l.slice(6)).join('\\n');
                                     if (data === '[DONE]') continue;
                                     appendChunk(respEl, data);
                                 } else {
                                     appendChunk(respEl, part);
                                 }
                                 respEl.scrollTop = respEl.scrollHeight;
                             }
                         }
                         if (buf && buf.trim()) appendChunk(respEl, buf);
                     } catch (err) {
                         respEl.textContent = 'Request failed: ' + String(err);
                     }
                });
              </script>
         </body>
         </html>
         '''
         return HTMLResponse(content=html)

@app.post('/ask')
async def ask(request: Request, req: AskRequest):
    llm = getattr(app.state, 'llm', None)
    if not llm:
        raise HTTPException(status_code=500, detail='LLM not loaded')

    # Ensure statelessness: only include the system and user messages from this request
    system_prompt = req.system_prompt or 'You are a helpful assistant. Keep answers concise.'
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': req.prompt}
    ]

    # Default generation params; individual requests may override.
    gen = {
        'temperature': float(os.environ.get('LLM_TEMPERATURE', '0.2')),
        'top_k': int(os.environ.get('LLM_TOP_K', '40')),
        'top_p': float(os.environ.get('LLM_TOP_P', '0.95')),
        'repeat_penalty': float(os.environ.get('LLM_REPEAT_PENALTY', '1.1')),
        'max_tokens': int(os.environ.get('LLM_MAX_TOKENS', '512')),
        'stop': ["<|eot_id|>"]
    }
    if req.generation_params:
        gen.update(req.generation_params)

    accept = request.headers.get('accept', '') or ''
    stream_requested = 'text/event-stream' in accept or request.query_params.get('stream') in ('1', 'true')

    if stream_requested:
        async def event_stream():
            try:
                # Try using llama_cpp streaming API if available
                for chunk in llm.create_chat_completion(messages=messages, stream=True, **gen):
                    try:
                        choice = (chunk.get('choices') or [{}])[0]
                        # Only emit actual content pieces. Some models emit a 'role' token first
                        # (e.g. "assistant") in the stream; ignore that and wait for 'content'.
                        delta = choice.get('delta') or choice.get('message') or {}
                        text_part = ''
                        if isinstance(delta, dict):
                            # prefer 'content' — do not emit 'role'
                            text_part = delta.get('content') or ''
                        else:
                            text_part = str(delta)
                        # strip any leading "assistant" artifact and skip empty results
                        if text_part:
                            text_part = re.sub(r'^\s*assistant[:\s]*', '', text_part, flags=re.I)
                            if text_part.strip():
                                yield f"data: {text_part}\n\n"
                                await asyncio.sleep(0)
                    except Exception:
                        # Non-fatal: continue streaming
                        continue
                # Signal done
                yield "event: done\ndata: [DONE]\n\n"
            except Exception:
                # Streaming not supported or failed; fall back to non-streaming chunked emit
                try:
                    response = llm.create_chat_completion(messages=messages, **gen)
                    content = response['choices'][0]['message'].get('content') if response and 'choices' in response else ''
                    content = (content or '').strip();
                    # Emit in small chunks so callers can process incrementally
                    chunk_size = 200
                    for i in range(0, len(content), chunk_size):
                        yield f"data: {content[i:i+chunk_size]}\n\n"
                        await asyncio.sleep(0)
                    yield "event: done\ndata: [DONE]\n\n"
                except Exception as e:
                    logging.exception('LLM generation error (stream fallback): %s', e)
                    yield f"event: error\ndata: {str(e)}\n\n"

        return StreamingResponse(event_stream(), media_type='text/event-stream')

    # Non-streaming (JSON) behaviour for backwards compatibility
    try:
        response = llm.create_chat_completion(messages=messages, **gen)
        content = response['choices'][0]['message'].get('content')
        text = content.strip() if content else ''
        return {'response': text}
    except Exception as e:
        logging.exception('LLM generation error: %s', e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post('/shutdown')
def shutdown(request: Request):
    # Optional token protection
    if SHUTDOWN_TOKEN:
        token = request.query_params.get('token') or request.headers.get('X-Shutdown-Token')
        if token != SHUTDOWN_TOKEN:
            raise HTTPException(status_code=403, detail='Invalid shutdown token')

    # Respond first, then schedule shutdown 0.2s later to allow response to reach client
    def _exit():
        logging.info('Shutting down LLM server as requested.')
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass
        os._exit(0)

    threading.Timer(0.2, _exit).start()
    return {'status': 'shutting_down'}

if __name__ == '__main__':
    # Simple CLI: start/stop/status. Use `start` (foreground) or `start --background`.
    import argparse
    import subprocess
    import signal
    import sys
    import time

    def start_foreground():
        import uvicorn
        uvicorn.run('llm_server:app', host=HOST, port=PORT, log_level='info')

    def start_background(logfile='/tmp/llm_server.log'):
        if PID_FILE.exists():
            try:
                existing = int(PID_FILE.read_text().strip())
                # check if running
                os.kill(existing, 0)
                print(f"Server already running with PID {existing}")
                return
            except Exception:
                pass
        cmd = [sys.executable, str(__file__), 'start']
        with open(logfile, 'ab') as out:
            p = subprocess.Popen(cmd, stdout=out, stderr=out, cwd=str(APP_DIR))
        # Give it a moment to start and write its pid file
        time.sleep(0.5)
        if p.poll() is None:
            print(f"Started background server (PID {p.pid}), logging to {logfile}")
            try:
                PID_FILE.write_text(str(p.pid))
            except Exception:
                pass
        else:
            print('Failed to start server. Check log:', logfile)

    def stop_server(timeout=3):
        if not PID_FILE.exists():
            print('No PID file found; server may not be running.')
            return
        try:
            pid = int(PID_FILE.read_text().strip())
        except Exception:
            print('Invalid PID file. Removing it.')
            PID_FILE.unlink(missing_ok=True)
            return
        try:
            os.kill(pid, signal.SIGTERM)
            # Wait briefly for process to exit
            for _ in range(timeout * 10):
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except Exception:
                    break
            else:
                # still running; force kill
                os.kill(pid, signal.SIGKILL)
            print(f'Stopped server (PID {pid}).')
        except ProcessLookupError:
            print('Process not found; removing stale PID file.')
        except PermissionError:
            print('Permission denied when trying to stop process.')
        finally:
            PID_FILE.unlink(missing_ok=True)

    def status_server():
        if not PID_FILE.exists():
            print('No PID file found; server not running.')
            return
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            print(f'Server appears to be running with PID {pid}.')
        except Exception:
            print('PID file exists but process not running. PID file may be stale.')

    parser = argparse.ArgumentParser(description='LLM server control')
    sub = parser.add_subparsers(dest='cmd')
    sub.add_parser('start', help='Start server in foreground')
    sbg = sub.add_parser('start-bg', help='Start server in background (detached)')
    sbg.add_argument('--log', default='/tmp/llm_server.log', help='Log file for background server')
    sub.add_parser('stop', help='Stop server using PID file')
    sub.add_parser('stop-all', help='Stop all running llm_server.py processes')
    sub.add_parser('restart-all', help='Stop all instances then start one background instance')
    sub.add_parser('status', help='Show server status')
    args = parser.parse_args()

    if args.cmd == 'start-bg':
        start_background(logfile=args.log)
    elif args.cmd == 'stop':
        stop_server()
    elif args.cmd == 'stop-all':
        # Attempt to kill all running llm_server.py processes
        try:
            import subprocess
            subprocess.run(['pkill', '-f', 'llm_server.py'], check=False)
            # remove known PID files
            for p in (PID_FILE, APP_DIR / '.llm_pid'):
                try:
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
            print('Requested stop for all llm_server.py processes.')
        except Exception as e:
            print('Failed to stop all processes:', e)
    elif args.cmd == 'restart-all':
        try:
            import subprocess
            subprocess.run(['pkill', '-f', 'llm_server.py'], check=False)
        except Exception:
            pass
        # remove PID files
        for p in (PID_FILE, APP_DIR / '.llm_pid'):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        # start one background instance
        start_background(logfile=args.log if hasattr(args, 'log') else '/tmp/llm_server.log')
    elif args.cmd == 'status':
        status_server()
    else:
        # Default: start foreground (also covers 'start' command)
        start_foreground()
