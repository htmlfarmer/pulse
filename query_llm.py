#!/usr/bin/env python3
import sys
import json
from pathlib import Path

try:
    from llama_cpp import Llama
except Exception as e:
    print(json.dumps({'error': f'llama_cpp not available: {e}'}))
    sys.exit(0)

import os
import logging

class SuppressStderr:
    def __enter__(self):
        self.original_stderr = os.dup(2)
        self.devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.devnull, 2)
    def __exit__(self, exc_type, exc_val, exc_tb):
        os.dup2(self.original_stderr, 2)
        os.close(self.devnull)

class AIModel:
    def __init__(self, model_path):
        self.llm = None
        self.config = {
            "llama_params": { "n_ctx": 2048, "n_threads": 8, "n_gpu_layers": 0, "verbose": False },
            "generation_params": {
                "temperature": 0.2, "top_k": 40, "top_p": 0.95,
                "repeat_penalty": 1.1, "max_tokens": 200, "stop": ["<|eot_id|>"]
            }
        }
        self.default_system_prompt = "You are a helpful assistant. Keep your answers concise."
        try:
            with SuppressStderr():
                self.llm = Llama(model_path=model_path, **self.config["llama_params"])
        except Exception as e:
            logging.error(f"Error loading model: {e}")

    def ask(self, user_question: str, system_prompt: str = None) -> str:
        if not self.llm:
            return "Error: Model not loaded."
        if system_prompt is None:
            system_prompt = self.default_system_prompt
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_question}
        ]
        try:
            response = self.llm.create_chat_completion(messages=messages, **self.config["generation_params"])
            content = response['choices'][0]['message'].get('content')
            return content.strip() if content else ''
        except Exception as e:
            logging.error(f"LLM generation error: {e}")
            return f"Error: {e}"

MODEL_PATH = "/home/asher/.lmstudio/models/lmstudio-community/gemma-3-1b-it-GGUF/gemma-3-1b-it-Q4_K_M.gguf"
#MODEL_PATH = "/home/asher/.lmstudio/models/lmstudio-community/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/DeepSeek-R1-Distill-Qwen-1.5B-Q8_0.gguf"

if not Path(MODEL_PATH).exists():
    print(json.dumps({'error': f'Model not found at {MODEL_PATH}'}))
    sys.exit(1)

try:
    payload = json.load(sys.stdin)
except Exception as e:
    print(json.dumps({'error': f'Invalid JSON input: {e}'}))
    sys.exit(1)

prompt = payload.get('prompt', '')
system_prompt = payload.get('system_prompt', None)

ai = AIModel(MODEL_PATH)
if not ai.llm:
    print(json.dumps({'error': 'LLM failed to load'}))
    sys.exit(1)

response = ai.ask(prompt, system_prompt=system_prompt)
print(json.dumps({'response': response}))
