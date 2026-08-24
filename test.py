from openai import OpenAI

# Connect to the local llama.cpp server instead of OpenAI's cloud servers
client = OpenAI(
    base_url="http://localhost:8081/v1", #
    api_key="test-key" # The server requires an API key string, but accepts any value
)

print("Thinking...\n")

# Generate the response
response = client.chat.completions.create(
    model="qwen2.5", # The specific name doesn't strictly matter for single-model servers
    messages=[
        {"role": "system", "content": "You are a helpful programming assistant."},
        {"role": "user", "content": "Write a Python function to solve the 8 Queens problem."}
    ],
    temperature=0.7,
    max_tokens=500
)

# Print the final output
print("Model Output:\n")
print(response.choices[0].message.content)