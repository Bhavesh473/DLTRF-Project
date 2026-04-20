# src/integration/client_libs/python/setup.py

from setuptools import setup, find_packages

setup(
    name='universal_logger_python',
    version='0.1.0',
    description='Python client library for the Universal Logging Microservice',
    author='Bhavesh',
    author_email='bhavesh@example.com',  # Replace with your email
    url='https://github.com/your-org/universal-logging-microservice',  # Replace with actual URL
    packages=find_packages(),
    install_requires=[
        'requests>=2.25.1',
    ],
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.7',
) 