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
