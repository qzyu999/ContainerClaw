import base64
data = open(r'secrets\enterprise_api_key.txt', 'r').read().strip()
print(f"Chars: {len(data)}")
print(f"Mod 4: {len(data) % 4}")
print(f"Has padding: {data.endswith('=')}")
# Try to decode
try:
    decoded = base64.b64decode(data)
    print(f"Valid base64: yes ({len(decoded)} decoded bytes)")
except Exception as e:
    print(f"Invalid base64: {e}")
    # Try with padding
    padded = data + "=" * (4 - len(data) % 4) if len(data) % 4 != 0 else data
    print(f"After padding ({len(padded)} chars):")
    try:
        decoded = base64.b64decode(padded)
        print(f"  Valid with padding: yes ({len(decoded)} decoded bytes)")
        print(f"  Fix: add {'=' * (4 - len(data) % 4)} to the end of the file")
    except Exception as e2:
        print(f"  Still invalid: {e2}")
