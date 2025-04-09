FROM python:3.12-slim

RUN useradd -m spyuser
WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/sessions && chown -R spyuser:spyuser /app

USER spyuser

CMD ["python", "main.py"]
