# Use a lightweight Python version
FROM python:3.13-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first (this caches the installation step)
COPY requirements.txt .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your code into the container
COPY . .

# Run as a non-root user; /app must stay writable for dates.db (WAL files)
RUN useradd --create-home --uid 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Unhealthy if the reminder loop hasn't ticked in the last 5 minutes
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "healthcheck.py"]

# The command to run your bot
CMD ["python", "main.py"]
