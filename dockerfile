# Use a specific version of Python 3.13 as the base image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /usr/src/app

# Install necessary system packages (if needed)
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Upgrade pip and install uv
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir uv

# Install other dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . .

# Create the virtual environment using uv
RUN uv venv

# Set the entry point for the container to activate the virtual environment and run the app
CMD ["/bin/sh", "-c", "source .venv/bin/activate && uv run app/agent.py"]
