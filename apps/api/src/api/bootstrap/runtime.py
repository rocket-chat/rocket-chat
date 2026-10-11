"""Environment-driven choices made once at start-up: driver, gateway, model, cluster mode."""

import logging
import os

from llm_gateway.gateway import LiteLLMGateway
from llm_gateway.stub import DeterministicStubGateway, stub_enabled
from sandbox_driver.docker_driver import DockerSandboxDriver
from sandbox_driver.fake_driver import FakeSandboxDriver
from sandbox_driver.k8s_driver import K8sSandboxDriver

from specifications.interfaces.sandbox import SandboxDriverProtocol

logger = logging.getLogger(__name__)

FALLBACK_MODEL = "openrouter/deepseek/deepseek-v4.1-flash"


def cluster_bus_enabled() -> bool:
    """Whether several API replicas may be serving at once (shared event bus configured)."""
    return os.getenv("ROCKET_EVENT_BUS", "memory").strip().lower() == "postgres"


def build_sandbox_driver() -> SandboxDriverProtocol:
    """The sandbox driver named by ``DEFAULT_SANDBOX_DRIVER`` / ``SANDBOX_DRIVER``."""
    driver_type = (
        os.getenv("DEFAULT_SANDBOX_DRIVER") or os.getenv("SANDBOX_DRIVER") or "docker"
    ).lower()
    if driver_type == "k8s":
        logger.info("Initializing Kubernetes Sandbox Driver (Production)...")
        return K8sSandboxDriver()
    if driver_type == "fake":
        logger.info("Initializing in-memory Fake Sandbox Driver (tests/CI)...")
        return FakeSandboxDriver()
    logger.info("Initializing Docker Sandbox Driver (Local)...")
    return DockerSandboxDriver()


def build_llm_gateway() -> LiteLLMGateway | DeterministicStubGateway:
    """The real LiteLLM gateway, or the deterministic stub when tests/CI ask for it."""
    if stub_enabled():
        logger.info("Initializing Deterministic Stub LLM Gateway (tests/CI)...")
        return DeterministicStubGateway()
    return LiteLLMGateway()


def default_model() -> str:
    model = os.getenv("ROCKET_DEFAULT_MODEL") or os.getenv("DEFAULT_MODEL") or FALLBACK_MODEL
    logger.debug("Configured default model: %s", model)
    return model
