import os
import sys
from src.ultraworker import UltraWorker

def test():
    # Provide dummy key if missing
    if not os.environ.get("NVIDIA_API_KEY"):
        os.environ["NVIDIA_API_KEY"] = "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"
    
    worker = UltraWorker()
    print("Testing UltraWorker streaming...")
    try:
        for event in worker.run_streaming("Create a file called 'hello.txt' with content 'hello world'"):
            print(event)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test()
