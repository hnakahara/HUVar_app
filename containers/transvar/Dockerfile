# TransVar 変換サービス（Python 3.9 — TransVar 2.5.10 の検証済みバージョン）
FROM python:3.9

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /srv

COPY containers/transvar/requirements.txt /srv/
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY containers/transvar/app.py /srv/app.py

EXPOSE 5000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
