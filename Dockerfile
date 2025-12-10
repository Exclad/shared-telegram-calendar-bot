# Use a lightweight Python version
FROM python:3.13-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first (this caches the installation step)
COPY requirements.txt .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your code into the container
COPY main.py .
COPY .env .

# (Optional) If you have an existing database, copy it. 
# If not, the bot will create one.
# COPY dates.db . 

# The command to run your bot
CMD ["python", "main.py"]
