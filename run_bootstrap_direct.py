import os
import sys
from src.main import main

if __name__ == "__main__":
    os.environ["OPENROUTER_API_KEY"] = "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"
    prompt = "Explain why it is useful to have a harness like Claw Code for an AI agent"
    print(f"Running bootstrap session with prompt: {prompt}")
    main(["bootstrap", prompt])
