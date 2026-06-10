FROM python:3.9-slim

# Install dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables
ENV USE_FIRESTORE=true
ENV PORT=8080

# Expose port
EXPOSE 8080

# Run the application
CMD ["python", "main.py"]
