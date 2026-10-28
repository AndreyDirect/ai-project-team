import os
import time
import random

from groq import Groq

from config import MODEL, RETRY_DELAYS, TEMPERATURE

client = Groq(api_key=os.environ["GROQ_API_KEY"])


def ask(role: str, prompt: str) -> str:
    """Отправляет запрос в Groq и повторяет при временных ошибках."""

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                messages=[
                    {
                        "role": "system",
                        "content": role
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            return response.choices[0].message.content

        except Exception as e:
            if attempt < len(RETRY_DELAYS):
                print(f"\nОшибка: {type(e).__name__}.")
                print(f"Повтор через {delay} секунд...")
                time.sleep(delay + random.randint(0, 3))
                continue
            raise