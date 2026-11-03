def pytest_addoption(parser):
    parser.addoption("--run-model", action="store_true", help="Run real Hebrew WhisperX alignment (downloads model on first use)")
    parser.addoption("--run-hebrew-models", action="store_true", help="Test both ivrit.ai models on CPU (downloads about 4.7 GB on first use)")
