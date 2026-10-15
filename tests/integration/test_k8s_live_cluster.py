"""The Kubernetes driver against a real cluster.

Opt-in: set ``ROCKET_K8S_LIVE=1`` and point ``KUBECONFIG`` at a throwaway k3d cluster that has the
sandbox image imported (``k3d image import rocket-chat-sandbox:dev -c <cluster>``). The test
refuses any context that is not named ``k3d-*`` and works only inside a namespace it creates and
deletes, so it can never touch a real cluster.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from kubernetes_asyncio import client, config
from sandbox_driver.exceptions import SandboxTimeoutError
from sandbox_driver.k8s_driver import K8sSandboxDriver

from specifications.interfaces.sandbox import SandboxStatus, WorkspaceSpec

IMAGE = os.getenv("ROCKET_K8S_LIVE_IMAGE", "rocket-chat-sandbox:dev")

pytestmark = pytest.mark.skipif(
    os.getenv("ROCKET_K8S_LIVE") != "1", reason="set ROCKET_K8S_LIVE=1 with a k3d KUBECONFIG"
)


@pytest_asyncio.fixture
async def driver() -> AsyncIterator[K8sSandboxDriver]:
    _, active = config.list_kube_config_contexts()
    assert active["name"].startswith("k3d-"), f"refusing to run against context {active['name']}"
    await config.load_kube_config()
    namespace = f"rocket-live-{uuid.uuid4().hex[:6]}"
    api = client.CoreV1Api()
    await api.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))
    try:
        yield K8sSandboxDriver(namespace=namespace)
    finally:
        await api.delete_namespace(namespace)
        await api.api_client.close()


def _spec(session_id: str, **extra: object) -> WorkspaceSpec:
    return WorkspaceSpec(
        session_id=session_id,
        tenant_org_id="org",
        tenant_user_id="user",
        container_image=IMAGE,
        require_non_root=True,
        **extra,  # type: ignore[arg-type]
    )


async def _wait_running(driver: K8sSandboxDriver, session_id: str) -> None:
    for _ in range(60):
        if await driver.get_status(session_id) == SandboxStatus.RUNNING:
            return
        await asyncio.sleep(2)
    raise AssertionError("sandbox never became Running")


async def test_lifecycle_isolation_and_file_io(driver: K8sSandboxDriver) -> None:
    session_id = f"live-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(_spec(session_id))
    await driver.start_sandbox(session_id)
    await _wait_running(driver, session_id)

    who = await driver.exec_command(session_id, "id -u && grep CapEff /proc/self/status")
    assert who.stdout.splitlines()[0] == "1000"
    assert who.stdout.split("CapEff:")[1].strip() == "0000000000000000"

    await driver.write_file(session_id, "/workspace/notes.txt", "kept\n" * 50_000)
    assert len(await driver.read_file(session_id, "/workspace/notes.txt")) == 250_000

    await driver.hibernate_sandbox(session_id)
    assert await driver.get_status(session_id) == SandboxStatus.HIBERNATED
    await driver.start_sandbox(session_id)
    await _wait_running(driver, session_id)
    assert (
        await driver.exec_command(session_id, "wc -c < /workspace/notes.txt")
    ).stdout.strip() == ("250000")
    await driver.destroy_workspace(session_id)


async def test_the_pty_runs_a_command_in_the_pod(driver: K8sSandboxDriver) -> None:
    session_id = f"live-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(_spec(session_id))
    await driver.start_sandbox(session_id)
    await _wait_running(driver, session_id)

    channel = await driver.open_pty(session_id, cols=80, rows=24)
    try:
        await channel.write(b"echo live-$((20+22))\n")
        seen = b""
        for _ in range(40):
            seen += await asyncio.wait_for(channel.read(), timeout=10)
            if b"live-42" in seen.replace(b"echo live-$((20+22))", b""):
                break
        assert b"live-42" in seen
    finally:
        await channel.close()
        await driver.destroy_workspace(session_id)


async def test_a_restricted_sandbox_cannot_reach_outside_its_allowlist(
    driver: K8sSandboxDriver,
) -> None:
    probe = "curl -s -o /dev/null -m 6 -w '%{http_code}' https://example.com || true"
    open_id = f"live-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(_spec(open_id))
    await driver.start_sandbox(open_id)
    await _wait_running(driver, open_id)
    baseline = (await driver.exec_command(open_id, probe, timeout_seconds=20)).stdout.strip()
    if not baseline.startswith("2") and not baseline.startswith("3"):
        pytest.skip("the cluster has no outbound internet, so enforcement cannot be observed")

    closed_id = f"live-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(_spec(closed_id, egress_allowlist=["203.0.113.0/24"]))
    await driver.start_sandbox(closed_id)
    await _wait_running(driver, closed_id)
    blocked = (await driver.exec_command(closed_id, probe, timeout_seconds=20)).stdout.strip()
    assert blocked in ("000", ""), f"expected the allowlist to block egress, got HTTP {blocked}"

    await driver.destroy_workspace(open_id)
    await driver.destroy_workspace(closed_id)


async def _started(driver: K8sSandboxDriver, **extra: object) -> str:
    session_id = f"live-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(_spec(session_id, **extra))
    await driver.start_sandbox(session_id)
    await _wait_running(driver, session_id)
    return session_id


async def _core(driver: K8sSandboxDriver) -> client.CoreV1Api:
    core, _ = await driver._ensure_clients()
    return core


async def test_destroying_a_workspace_leaves_nothing_behind(driver: K8sSandboxDriver) -> None:
    session_id = await _started(driver, egress_allowlist=["203.0.113.0/24"])
    core = await _core(driver)
    networking = await driver._networking()
    assert (await core.list_namespaced_pod(driver.namespace)).items
    assert (await networking.list_namespaced_network_policy(driver.namespace)).items

    await driver.destroy_workspace(session_id)

    for _ in range(30):
        pods = (await core.list_namespaced_pod(driver.namespace)).items
        claims = (await core.list_namespaced_persistent_volume_claim(driver.namespace)).items
        policies = (await networking.list_namespaced_network_policy(driver.namespace)).items
        if not (pods or claims or policies):
            return
        await asyncio.sleep(1)
    raise AssertionError(
        f"left behind: {len(pods)} pods, {len(claims)} claims, {len(policies)} policies"
    )


async def test_a_workspace_survives_an_api_restart(driver: K8sSandboxDriver) -> None:
    session_id = await _started(driver)
    await driver.write_file(session_id, "/workspace/keep.txt", "still here\n")
    await driver.hibernate_sandbox(session_id)

    restarted = K8sSandboxDriver(namespace=driver.namespace)
    await restarted.adopt_existing_sandboxes()
    assert session_id in restarted._last_activity, "the restarted API must track the workspace"

    await restarted.ensure_workspace(_spec(session_id))
    await restarted.start_sandbox(session_id)
    await _wait_running(restarted, session_id)
    assert await restarted.read_file(session_id, "/workspace/keep.txt") == "still here\n"


async def test_an_idle_sandbox_is_paused_and_resumes_with_its_files(
    driver: K8sSandboxDriver,
) -> None:
    session_id = await _started(driver)
    await driver.write_file(session_id, "/workspace/work.txt", "unsaved thoughts\n")
    driver.record_activity(session_id)

    assert await driver.pause_inactive_sandboxes(threshold_seconds=0) == [session_id]
    assert await driver.get_status(session_id) == SandboxStatus.HIBERNATED

    await driver.start_sandbox(session_id)
    await _wait_running(driver, session_id)
    assert await driver.read_file(session_id, "/workspace/work.txt") == "unsaved thoughts\n"


async def test_a_command_past_its_deadline_is_killed(driver: K8sSandboxDriver) -> None:
    session_id = await _started(driver)

    started = time.monotonic()
    with pytest.raises(SandboxTimeoutError):
        await driver.exec_command(session_id, "sleep 120", timeout_seconds=3)
    assert time.monotonic() - started < 30

    survivors = await driver.exec_command(session_id, "pgrep -x sleep || true")
    assert survivors.stdout.strip() == "", "the timed-out process must not keep running"


async def test_file_tools_work_on_a_real_pod(driver: K8sSandboxDriver) -> None:
    session_id = await _started(driver)
    await driver.write_file(session_id, "/workspace/src/app.py", "one\ntwo\nthree\n")

    listing = await driver.list_files(session_id, "/workspace", max_depth=2)
    assert "/workspace/src/app.py" in [entry.path for entry in listing]

    assert await driver.apply_patch(
        session_id, "/workspace/src/app.py", "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
    )
    assert await driver.read_file(session_id, "/workspace/src/app.py") == "one\nTWO\nthree\n"
    assert await driver.read_file(session_id, "/workspace/src/app.py", 2, 2) == "TWO\n"


async def test_eviction_protection_is_toggled_on_the_pod(driver: K8sSandboxDriver) -> None:
    session_id = await _started(driver)
    core = await _core(driver)
    name = driver._pod_name(session_id)

    await driver.set_eviction_protection(session_id, True)
    pod = await core.read_namespaced_pod(name, driver.namespace)
    assert pod.metadata.annotations["cluster-autoscaler.kubernetes.io/safe-to-evict"] == "false"
    assert pod.metadata.annotations["karpenter.sh/do-not-disrupt"] == "true"

    await driver.set_eviction_protection(session_id, False)
    pod = await core.read_namespaced_pod(name, driver.namespace)
    assert pod.metadata.annotations["cluster-autoscaler.kubernetes.io/safe-to-evict"] == "true"


async def test_the_pod_carries_the_organizations_limits_and_hardening(
    driver: K8sSandboxDriver,
) -> None:
    session_id = await _started(driver)
    core = await _core(driver)
    pod = await core.read_namespaced_pod(driver._pod_name(session_id), driver.namespace)

    container = pod.spec.containers[0]
    assert container.resources.limits["cpu"] in ("2", "2000m")
    assert container.resources.limits["memory"] == "4Gi"
    context = container.security_context
    assert context.allow_privilege_escalation is False
    assert context.capabilities.drop == ["ALL"]
    assert context.seccomp_profile.type == "RuntimeDefault"
    assert pod.spec.security_context.run_as_non_root is True
    assert pod.spec.share_process_namespace is True, "orphaned processes need a reaper"
    assert pod.spec.automount_service_account_token is False, (
        "agent code must not hold a cluster credential"
    )


async def _http_status(driver: K8sSandboxDriver, session_id: str, url: str) -> str:
    probe = f"curl -sk -o /dev/null -m 6 -w '%{{http_code}}' {url} || true"
    return (await driver.exec_command(session_id, probe, timeout_seconds=20)).stdout.strip()


async def test_an_allowlisted_host_is_reachable_and_everything_else_is_not(
    driver: K8sSandboxDriver,
) -> None:
    open_id = await _started(driver)
    if not (await _http_status(driver, open_id, "https://example.com")).startswith(("2", "3")):
        pytest.skip("the cluster has no outbound internet")

    closed_id = await _started(driver, egress_allowlist=["example.com"])
    assert (await _http_status(driver, closed_id, "https://example.com")).startswith(("2", "3"))
    assert await _http_status(driver, closed_id, "https://www.iana.org") in ("000", "")


async def test_block_internal_subnets_keeps_the_internet_but_not_the_cluster(
    driver: K8sSandboxDriver,
) -> None:
    open_id = await _started(driver)
    if not (await _http_status(driver, open_id, "https://example.com")).startswith(("2", "3")):
        pytest.skip("the cluster has no outbound internet")
    # The API server's in-cluster address answers (with 401/403) for an unrestricted pod.
    assert await _http_status(driver, open_id, "https://kubernetes.default.svc") not in ("000", "")

    closed_id = await _started(driver, block_internal_subnets=True)
    assert (await _http_status(driver, closed_id, "https://example.com")).startswith(("2", "3"))
    assert await _http_status(driver, closed_id, "https://kubernetes.default.svc") in ("000", "")


CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "platform"

# Every call the driver makes, as (verb, resource, subresource): the role must allow exactly these.
DRIVER_VERBS = [
    ("create", "pods", ""), ("get", "pods", ""), ("list", "pods", ""), ("delete", "pods", ""),
    ("patch", "pods", ""), ("create", "pods", "exec"),
    ("create", "persistentvolumeclaims", ""), ("get", "persistentvolumeclaims", ""),
    ("list", "persistentvolumeclaims", ""), ("delete", "persistentvolumeclaims", ""),
]  # fmt: skip
DENIED_VERBS = [
    ("get", "secrets", ""), ("list", "secrets", ""), ("watch", "pods", ""), ("patch", "persistentvolumeclaims", ""),
    ("create", "deployments", ""), ("delete", "namespaces", ""), ("list", "nodes", ""), ("create", "pods/attach", ""),
]  # fmt: skip


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
async def test_the_charts_sandbox_role_grants_exactly_what_the_driver_uses(
    driver: K8sSandboxDriver,
) -> None:
    from kubernetes_asyncio import utils

    helm = await asyncio.create_subprocess_exec(
        "helm", "template", "rbac-check", str(CHART), "--namespace", driver.namespace,
        "--set", f"sandbox.namespace={driver.namespace}",
        "--set", "backend.encryption.masterKey=x", "--set", "auth.sessionSecret=" + "x" * 40,
        "--show-only", "templates/backend/rbac.yaml", "--show-only", "templates/backend/serviceaccount.yaml",
        stdout=asyncio.subprocess.PIPE,
    )  # fmt: skip
    stdout, _ = await helm.communicate()
    assert helm.returncode == 0, "helm template failed"
    rendered = stdout.decode()
    manifest = Path(os.environ.get("TMPDIR", "/tmp")) / f"rbac-{uuid.uuid4().hex[:6]}.yaml"
    manifest.write_text(rendered)
    try:
        core = await _core(driver)
        await utils.create_from_yaml(core.api_client, str(manifest), namespace=driver.namespace)
    finally:
        manifest.unlink(missing_ok=True)

    import yaml

    service_account = next(
        d["metadata"]["name"]
        for d in yaml.safe_load_all(rendered)
        if d and d["kind"] == "ServiceAccount"
    )
    user = f"system:serviceaccount:{driver.namespace}:{service_account}"
    authz = client.AuthorizationV1Api(api_client=core.api_client)

    async def allowed(verb: str, resource: str, subresource: str, namespace: str) -> bool:
        review = client.V1SubjectAccessReview(
            spec=client.V1SubjectAccessReviewSpec(
                user=user,
                resource_attributes=client.V1ResourceAttributes(
                    namespace=namespace,
                    verb=verb,
                    resource=resource,
                    subresource=subresource or None,
                ),
            )
        )
        return bool((await authz.create_subject_access_review(review)).status.allowed)

    for verb, resource, subresource in DRIVER_VERBS:
        assert await allowed(verb, resource, subresource, driver.namespace), (
            f"missing {verb} {resource}/{subresource}"
        )
    for verb, resource, subresource in DENIED_VERBS:
        assert not await allowed(verb, resource, subresource, driver.namespace), (
            f"too broad: {verb} {resource}"
        )
    assert not await allowed("get", "pods", "", "default"), (
        "the role must not reach other namespaces"
    )
    assert json.dumps(DRIVER_VERBS)  # keep the table importable for docs


def _process_id(message: str) -> str:
    from agent_core.tools import processes

    return next(word for word in message.split() if processes.is_valid_id(word))


async def test_background_processes_run_and_stop_in_a_pod(driver: K8sSandboxDriver) -> None:
    """The process tools work through the Kubernetes exec path, as the unprivileged user."""
    from agent_core.tools import processes

    session_id = f"live-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(_spec(session_id))
    await driver.start_sandbox(session_id)
    await _wait_running(driver, session_id)

    started = await processes.start_process(
        driver, session_id, "python3 -m http.server 8123", name="static site", port=8123
    )
    process_id = _process_id(started)
    reachable = await driver.exec_command(
        session_id,
        "for i in 1 2 3 4 5 6 7 8 9 10; do curl -sf http://127.0.0.1:8123/ >/dev/null && echo up && exit 0; sleep 1; done; exit 1",
        timeout_seconds=30,
    )
    assert "up" in reachable.stdout

    listed = await processes.list_processes(driver, session_id)
    assert [(p.id, p.state, p.port) for p in listed] == [(process_id, "running", 8123)]
    assert "GET /" in await processes.read_output(driver, session_id, process_id)

    assert "stopped" in await processes.stop_process(driver, session_id, process_id)
    gone = await driver.exec_command(
        session_id, "curl -sf -m 2 http://127.0.0.1:8123/ || echo down", timeout_seconds=15
    )
    assert "down" in gone.stdout, "stopping the process must free its port"


async def test_a_process_group_dies_together_in_a_pod(driver: K8sSandboxDriver) -> None:
    from agent_core.tools import processes

    session_id = f"live-{uuid.uuid4().hex[:6]}"
    await driver.ensure_workspace(_spec(session_id))
    await driver.start_sandbox(session_id)
    await _wait_running(driver, session_id)

    tree = _process_id(
        await processes.start_process(driver, session_id, "(sleep 300 &) ; sleep 300")
    )
    await processes.stop_process(driver, session_id, tree)
    left = await driver.exec_command(session_id, "pgrep -x sleep || echo none", timeout_seconds=15)
    assert "none" in left.stdout


async def test_the_session_environment_never_appears_on_a_command_line(
    driver: K8sSandboxDriver,
) -> None:
    session_id = await _started(driver)
    secret = f"cap-{uuid.uuid4().hex}"
    driver.set_session_env(session_id, {"ROCKET_TEST_CAPABILITY": secret})

    seen = await driver.exec_command(session_id, 'printf %s "$ROCKET_TEST_CAPABILITY"')
    running = asyncio.create_task(driver.exec_command(session_id, "sleep 4"))
    await asyncio.sleep(1.5)
    listed = await driver.exec_command(
        session_id, "cat /proc/[0-9]*/cmdline 2>/dev/null | tr '\\0' ' '"
    )
    await running

    assert seen.stdout == secret, "the command sees its environment"
    assert "sleep 4" in listed.stdout, "the concurrent command was listed"
    assert secret not in listed.stdout, "no process argv carries the value"


async def test_a_restricted_sandbox_reaches_the_platform_pods_and_nothing_else(
    driver: K8sSandboxDriver,
) -> None:
    from sandbox_driver.egress import PlatformPeer

    labels = {"app.kubernetes.io/name": "rocket-live-platform"}
    api = client.CoreV1Api()
    await api.create_namespaced_pod(
        driver.namespace,
        client.V1Pod(
            metadata=client.V1ObjectMeta(name="platform", labels=labels),
            spec=client.V1PodSpec(
                containers=[
                    client.V1Container(
                        name="http",
                        image=IMAGE,
                        image_pull_policy="IfNotPresent",
                        command=["python3", "-m", "http.server", "8000"],
                    )
                ]
            ),
        ),
    )
    for _ in range(60):
        pod = await api.read_namespaced_pod("platform", driver.namespace)
        if pod.status.phase == "Running" and pod.status.pod_ip:
            break
        await asyncio.sleep(2)
    address = pod.status.pod_ip
    driver._platform_peer = PlatformPeer(driver.namespace, labels, 8000)

    session_id = await _started(driver, block_internal_subnets=True)
    await asyncio.sleep(3)  # let the policy controller program the rule

    assert (await _http_status(driver, session_id, f"http://{address}:8000/")).startswith("2")
    assert await _http_status(driver, session_id, "https://kubernetes.default.svc") in ("000", "")


async def test_a_timed_out_command_reports_what_it_printed(driver: K8sSandboxDriver) -> None:
    from agent_core.tools import tier2

    session_id = await _started(driver)

    out = await tier2.bash_exec(
        driver, session_id, "echo before-the-hang; echo err-line >&2; sleep 30", timeout_seconds=3
    )

    assert "timed out after 3s" in out and "before-the-hang" in out and "err-line" in out


async def test_an_edit_keeps_a_scripts_executable_bit(driver: K8sSandboxDriver) -> None:
    session_id = await _started(driver)
    await driver.exec_command(
        session_id,
        "printf '#!/bin/sh\\necho v1\\n' > /workspace/run.sh && chmod 755 /workspace/run.sh",
    )

    await driver.write_file(session_id, "/workspace/run.sh", "#!/bin/sh\necho v2\n")
    ran = await driver.exec_command(session_id, "stat -c %a /workspace/run.sh && /workspace/run.sh")

    assert ran.stdout.split() == ["755", "v2"]
