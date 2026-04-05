import os
from src.llm import LLMClient
from src.agent import SYSTEM_PROMPT

def test():
    # Use Llama 3 70B as active model instead to test its behavior
    os.environ["CLAW_MODEL"] = "nvidia:llama-3.3-70b-instruct"
    client = LLMClient()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Create a python file main.py that prints hello world"}
    ]
    print("Testing prompt generation...")
    res = client.chat(messages)
    print("----- RESULT -----")
    print(res)
    
if __name__ == "__main__":
    test()
