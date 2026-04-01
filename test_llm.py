import sys
from src.llm import OpenRouterClient

def test():
    key = "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"
    client = OpenRouterClient(api_key=key, model="openrouter/auto:free")
    print(f"Testing with model: {client.model}")
    messages = [{"role": "user", "content": "Hello! Say just 'Success' if you can read this."}]
    print("Requesting response...")
    response = client.chat(messages)
    print(f"Response: {response}")

if __name__ == "__main__":
    test()
