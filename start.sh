#!/bin/bash
cd "$(dirname "$0")"
export DEEPSEEK_API_KEY=sk-d44af7e413544881957936d4408f2f63
export DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
exec python -m uvicorn main:app --host 0.0.0.0 --port 8080
