import os
import random
import time
from groq import Groq, APIConnectionError, APIStatusError
from config import MODEL, RETRY_DELAYS, TEMPERATURE


def ask(role: str, prompt: str, *, model=None, temperature=None) -> str:
    selected_model = MODEL if model is None else model
    selected_temperature = TEMPERATURE if temperature is None else temperature
    if not isinstance(selected_model, str) or not selected_model.strip():
        raise ValueError("Не указана модель для запроса.")
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Не задан GROQ_API_KEY. Проверьте окружение или Secrets.")
    delays = list(RETRY_DELAYS)
    for delay in delays:
        if not isinstance(delay, (float, int)) or delay < 0:
            raise ValueError("RETRY_DELAYS должен содержать неотрицательные числа.")
    attempts = max(1, len(delays))
    with Groq(api_key=key, max_retries=0, timeout=120.0) as client:
        for attempt in range(attempts):
            try:
                response = client.chat.completions.create(
                    model=selected_model, temperature=selected_temperature,
                    messages=[{"role": "system", "content": role}, {"role": "user", "content": prompt}],
                )
                text = response.choices[0].message.content if response.choices else None
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Модель вернула пустой ответ.")
                return text
            except (APIConnectionError, APIStatusError) as exc:
                transient = isinstance(exc, APIConnectionError) or (
                    isinstance(exc, APIStatusError)
                    and (exc.status_code in {408, 409, 429} or exc.status_code >= 500)
                )
                if not transient or attempt + 1 >= attempts:
                    raise
                delay = delays[attempt] + random.uniform(0, 3)
                print(f"Временная ошибка {type(exc).__name__}. Повтор {attempt + 2}/{attempts} через {delay:.1f} сек.")
                time.sleep(delay)
    raise RuntimeError("Не удалось получить ответ модели.")
