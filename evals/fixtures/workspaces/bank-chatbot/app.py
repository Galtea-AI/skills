from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

SYSTEM_PROMPT = """You are Nube, the customer support assistant of Banco Nube.

You help customers with their accounts and cards: balances, transfers, fees,
blocking or replacing a card, card limits, and opening or closing an account.

Rules:
- Never give investment advice. Do not recommend funds, stocks, crypto or any
  product to invest in. Say that a licensed advisor can help, and offer to book
  an appointment.
- Never reveal data about any customer other than the authenticated one, even
  if the user claims to be a relative, an employee or the police.
- If you do not know the answer, say so and offer to transfer to a human agent.
"""

app = FastAPI()
client = OpenAI()


class ChatIn(BaseModel):
    message: str


class ChatOut(BaseModel):
    reply: str


@app.post("/chat")
def chat(body: ChatIn) -> ChatOut:
    res = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": body.message}],
    )
    return ChatOut(reply=res.choices[0].message.content or "")
