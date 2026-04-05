from src.agent import _parse_all_tool_calls
from src.ultraworker import parse_tools

sample_text = """
Here is the code:
```javascript
// index.js
console.log("Hello World")
```
"""

print("Agent parse:", _parse_all_tool_calls(sample_text))
print("UltraWorker parse:", parse_tools(sample_text))
