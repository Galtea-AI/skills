# Nube support bot

Customer support chatbot for Banco Nube, a Spanish retail bank. It answers in
Spanish or English, whichever the customer writes in.

Run locally:

    pip install -r requirements.txt
    OPENAI_API_KEY=... uvicorn app:app --port 8000

    curl -X POST localhost:8000/chat -H 'content-type: application/json' \
      -d '{"message": "¿Cómo bloqueo mi tarjeta?"}'
