import time
import json
from datetime import datetime
from functools import wraps
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

client = genai.Client()

async def stream(name, model, content, role, **kwargs):
    print(f"[{name}]: ", end="")
    full_text = ""
    st = time.perf_counter()
    ttft = None
    async for chunk in await client.aio.models.generate_content_stream(
        model=model,
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=role,
            temperature=0.2,
            **kwargs
        )
    ):
        if chunk.text:
            if not full_text:
                ttft = time.perf_counter() - st
            full_text += chunk.text
            print(chunk.text, end="", flush=True)
    total_time = time.perf_counter() - st      
    print()
    if ttft is not None:
        print(f"Время первого токена:{ttft:.5f} seconds")
    print(f"Время полного вывода: {total_time:.2f}")
    return full_text

async def generate(name, model, content, role, **kwargs):
    full_text = ""
    response = await client.aio.models.generate_content(
        model=model,
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=role,
            temperature=0.3,
            **kwargs
        )
    )
    full_text += response.text
    print(f"[{name}]: {full_text}")  
    return full_text

def agent_logger(func):
    @wraps(func)
    async def wrapper(self, task, *args, **kwargs):
        self.tasks_done += 1
        print(f"[{self.name}] -> {task}")
        start_main = time.perf_counter()
        try:
            full_text = await func(self, task, *args, **kwargs)
            end_main = time.perf_counter() - start_main
            if full_text:
                print(f"[{self.name}] -> Time taken: {end_main:.3f}c")
                self.total_char += len(full_text)
                self.logs.append({
                    "timestamp": datetime.now().isoformat(),
                    "agent": self.name,
                    "task": task,
                    "response": full_text,
                    "time_taken": round(end_main, 2),
                    "chars": len(full_text)
                })
            return full_text
        except Exception as e:
            print(f"Error in agent {self.name}: {e}")
            self.logs.append({
                "timestamp": datetime.now().isoformat(),
                "agent": self.name,
                "task": task,
                "error": str(e)
            })
            return None
    return wrapper

class GeminiAgent:
    def __init__(self, name, role, model="gemini-2.0-flash"):
        self.name = name
        self.role = role
        self.model_name = model
        self.logs = []
        self.tasks_done = 0
        self.total_char = 0
        
    @agent_logger
    async def run(self, task, stream_mode: bool = True, **kwargs):
        if stream_mode:
            full_text = await stream(self.name, self.model_name, task, self.role, **kwargs)
        else:
            full_text = await generate(self.name, self.model_name, task, self.role, **kwargs)
        return full_text

    def get_stats(self):
        return {
            "agent": self.name,
            "tasks_done": self.tasks_done,
            "total_char": self.total_char,
            "avr_char": self.total_char // self.tasks_done if self.tasks_done > 0 else 0
        }

    def save_logs(self, filename=None):
        filename = filename or f"{self.name.lower()}_logs.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.logs, f, ensure_ascii=False, indent=2)
        print(f"[{self.name}] Logs saved to {filename}")

    def __str__(self):
        return f"GeminiAgent({self.name}, tasks={self.tasks_done}, chars={self.total_char})"