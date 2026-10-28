"""Просмотр отчёта: верхняя кнопка скачивания и отдельное поле ошибок."""
import json
import re
import streamlit as st
from storage import load_run_text, HISTORY_FILES

ORDER = ['04_document.md', '01_plan.md', '02_analysis.md', '03_architecture.md',
         '05_review.md', '00_brief.md', '00_knowledge.md']
STATUS = {'completed': 'Завершён', 'failed': 'Ошибка', 'running': 'Выполняется', 'unknown': 'Нет статуса'}


def read_file(run_id, filename):
    try:
        return load_run_text(run_id, filename), None
    except FileNotFoundError:
        return None, 'Файл отсутствует или ещё не создан.'
    except (OSError, ValueError) as exc:
        return None, f'Не удалось прочитать файл: {exc}'


def explain_error(message):
    text = str(message)
    lower = text.lower()
    if '429' in text or 'ratelimiterror' in lower or 'rate_limit_exceeded' in lower:
        cause = 'Groq отклонил запрос из-за ограничения частоты запросов или объёма токенов.'
        if 'tokens per minute' in lower or '(tpm)' in lower:
            cause = 'Превышен лимит токенов в минуту (TPM) у Groq. Это ограничение скорости обработки, а не ошибка документа.'
        wait = re.search(r'try again in\s+([0-9.]+)s', text, re.IGNORECASE)
        if wait:
            cause += f' В момент ошибки сервер рекомендовал ждать около {float(wait.group(1)):.1f} сек.'
        return cause, 'Подождите и используйте «Продолжить» в истории. Не запускайте несколько проектов параллельно. Время из старой ошибки не является текущим таймером.'
    if '401' in text or 'authenticationerror' in lower or 'invalid_api_key' in lower:
        return 'Провайдер не принял API-ключ.', 'Проверьте GROQ_API_KEY в переменных окружения или Secrets. Не публикуйте ключ в коде.'
    if '403' in text or 'permissiondenied' in lower:
        return 'Провайдер отказал в доступе к ресурсу.', 'Проверьте права ключа и доступ к указанной модели.'
    if 'timeout' in lower or 'timed out' in lower:
        return 'Истекло время ожидания ответа провайдера.', 'Попробуйте продолжить запуск позднее. Проверьте размер запроса и доступность сервиса.'
    if 'apiconnectionerror' in lower or 'connection' in lower:
        return 'Не удалось установить или сохранить соединение с провайдером.', 'Проверьте соединение с интернетом и доступность API, затем продолжите запуск.'
    if re.search(r'\b50[0234]\b', text):
        return 'Провайдер сообщил о временной серверной ошибке.', 'Повторите продолжение позднее.'
    if 'groq_api_key' in lower:
        return 'Ключ GROQ_API_KEY не задан или недоступен приложению.', 'Настройте переменную окружения либо Secrets и перезапустите приложение.'
    if 'пустой ответ' in lower:
        return 'Модель не вернула непустой текст результата.', 'Повторите неудавшийся этап через «Продолжить».'
    if 'context_length' in lower or 'maximum context' in lower:
        return 'Запрос превышает допустимый контекст модели.', 'Сократите бриф, документы или результаты предыдущих этапов. Одна пауза не устранит эту причину.'
    return 'Точная причина автоматически не определена.', 'Откройте технические детали ниже. Не запускайте проект заново, пока не проверите сообщение.'


def render_error_field(row, log):
    attempt_errors = []
    attempts = log.get('attempts', [])
    if isinstance(attempts, list):
        attempt_errors = [str(a['error']) for a in attempts if isinstance(a, dict) and a.get('error')]
    messages = log.get('errors') or row.get('errors') or attempt_errors
    if not isinstance(messages, list):
        messages = [str(messages)]
    messages = [str(m) for m in messages if m]
    with st.container(border=True):
        st.subheader('Ошибки и причины')
        if not messages:
            st.caption('В журнале нет записанных ошибок. Это не подтверждает успешное завершение: смотрите статус запуска.')
            return
        recovered = row.get('status') == 'completed'
        if recovered:
            st.info('Запуск завершён. Ниже — ошибки предыдущих попыток, сохранённые для диагностики.')
        latest = messages[-1]
        cause, action = explain_error(latest)
        if recovered:
            st.write('Последняя записанная причина: ' + cause)
        else:
            st.error('Причина: ' + cause)
        stage = log.get('failed_stage') or log.get('current_stage')
        stage_names = {'plan':'Project Manager', 'analysis':'Analyst', 'architecture':'Architect', 'document':'Writer', 'review':'Reviewer'}
        if stage:
            st.caption('Этап: ' + stage_names.get(str(stage), str(stage)))
        match = re.search(r'Попытка\s+(\d+)', latest)
        if match:
            st.caption('Номер попытки: ' + match.group(1))
        st.write('Что делать: ' + action)
        with st.expander(f'Технические детали и история ошибок ({len(messages)})'):
            for index, message in enumerate(messages, 1):
                st.text(f'{index}. {message}')


def render_report_dialog(row):
    run_id = row['run_id']
    st.subheader(row['project_name'])
    st.caption(f"ID: {run_id} · {row.get('created_at') or 'Дата не указана'} · {row.get('model', 'Модель не указана')}")
    st.caption('Статус: ' + STATUS.get(row.get('status'), 'Нет статуса') + '. Результаты сохраняются на сервере автоматически.')
    content = {name: read_file(run_id, name) for name in ORDER}
    raw_log, log_error = read_file(run_id, 'run_log.json')
    try:
        log = json.loads(raw_log) if raw_log else {}
        if not isinstance(log, dict):
            log = {}
    except ValueError:
        log = {}
    order = list(ORDER)
    fallback = next((name for name in ['04_document.md', '05_review.md', '03_architecture.md', '02_analysis.md', '01_plan.md', '00_brief.md', '00_knowledge.md']
                     if content[name][0] and content[name][0].strip()), None)
    if fallback:
        order = [fallback] + [name for name in order if name != fallback]
    labels = [HISTORY_FILES.get(name, name) for name in order] + ['Журнал']
    by_label = dict(zip(labels, order + ['run_log.json']))
    tab_key = 'report_active_tab_' + run_id
    if st.session_state.get(tab_key) not in labels:
        st.session_state[tab_key] = labels[0]
    # The placeholder stays above error details and document content.
    toolbar = st.container()
    for warning in row.get('warnings', []):
        st.warning(warning)
    if log_error:
        st.warning('Журнал недоступен: ' + log_error)
    elif raw_log and not log:
        st.warning('Журнал пуст или его JSON не удалось прочитать.')
    render_error_field(row, log)
    if not content['04_document.md'][0]:
        st.info('Итоговый документ ещё не доступен. По умолчанию открыт последний сохранённый результат.')
    tabs = st.tabs(labels, key=tab_key, on_change='rerun')
    active_label = st.session_state.get(tab_key, labels[0])
    filename = by_label.get(active_label, order[0])
    download_text = raw_log if filename == 'run_log.json' else content[filename][0]
    with toolbar:
        st.caption('Скачать открытый документ: ' + active_label)
        st.download_button(
            '⬇️ Скачать — ' + active_label,
            data=download_text or '', file_name=filename,
            mime='application/json' if filename == 'run_log.json' else 'text/markdown',
            key='report_top_download_' + run_id + '_' + filename,
            type='primary', use_container_width=True, on_click='ignore',
            disabled=not bool(download_text and download_text.strip()),
        )
    for tab, name in zip(tabs[:-1], order):
        with tab:
            text, error = content[name]
            if text and text.strip():
                st.markdown(text)
            else:
                st.info(error or 'Файл пуст.')
    with tabs[-1]:
        if raw_log is None:
            st.warning(log_error)
        elif log:
            st.json(log)
        else:
            st.text(raw_log)
