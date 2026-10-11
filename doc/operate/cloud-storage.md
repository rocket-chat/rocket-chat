# Cloud CSIs & Storage Classes Reference

In Kubernetes mode, Rocket Chat provisions a dedicated `PersistentVolumeClaim` (PVC) for every active agent session to retain workspace repositories, modified code, and git indices across turn hibernations.

This guide provides deep architectural guidance on configuring Container Storage Interface (CSI) drivers across major cloud providers.

---

## 1. Cloud Provider CSI Matrix

| Cloud Provider | Recommended StorageClass | CSI Driver | Recommended Access Mode |
| :--- | :--- | :--- | :--- |
| **Amazon Web Services (AWS EKS)** | `gp3` | `ebs.csi.aws.com` | `ReadWriteOnce` (RWO) |
| **Microsoft Azure (Azure AKS)** | `managed-csi` | `disk.csi.azure.com` | `ReadWriteOnce` (RWO) |
| **Google Cloud Platform (GCP GKE)** | `pd-balanced` | `pd.csi.storage.gke.io` | `ReadWriteOnce` (RWO) |
| **Local / Kind / Minikube** | `standard` or `local-path` | `rancher.io/local-path` | `ReadWriteOnce` (RWO) |

---

## 2. AWS EKS Configuration (`gp3`)

AWS Elastic Block Store (EBS) `gp3` is the optimal balance of price, latency, and burst performance for developer workspaces:

### A. Prerequisites
Ensure the AWS EBS CSI driver addon is installed on your EKS cluster:
```bash
aws eks describe-addon --cluster-name <cluster-name> --addon-name aws-ebs-csi-driver
```

### B. StorageClass Definition
```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
provisioner: ebs.csi.aws.com
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
parameters:
  type: gp3
  iops: "3000"
  throughput: "125"
  encrypted: "true"
```

> [!IMPORTANT]
> Always set `volumeBindingMode: WaitForFirstConsumer`. This ensures the PVC is provisioned in the exact Availability Zone (AZ) where the initial sandbox pod is scheduled, avoiding cross-AZ scheduling deadlocks.

---

## 3. Microsoft Azure AKS Configuration (`managed-csi`)

Azure Disk CSI supports dynamic volume provisioning with managed SSDs:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: managed-csi
provisioner: disk.csi.azure.com
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
parameters:
  skuName: Premium_LRS # High performance NVMe-backed storage
```

---

## 4. Google Cloud GKE Configuration (`pd-balanced`)

GCP Persistent Disk `pd-balanced` delivers SSD-like IOPS at lower costs:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: pd-balanced
provisioner: pd.csi.storage.gke.io
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
parameters:
  type: pd-balanced
```

---

## 5. Storage Detachment Latency & Soft Node Pinning

Cloud block storage (AWS EBS, Azure Disk, GCP PD) is physically attached to individual EC2/VM hypervisors. When a pod running on `Node A` terminates and is rescheduled on `Node B`:

1. Kubernetes sends an unmount request to `Node A`.
2. The cloud provider's control plane detaches the volume from VM `A`. This cloud detachment handshake typically requires **60 to 90 seconds**.
3. The cloud provider attaches the volume to VM `B`.
4. Kubernetes formats and mounts the volume on `Node B`.

### How Rocket Chat Bypasses the 90-Second Delay
When a session hibernates (`scale-to-zero`), `K8sSandboxDriver` records the scheduled node name (`scheduled_node_name`).

When the session is resumed:
- The driver injects **Soft Node Affinity** preferring `scheduled_node_name`:
  ```yaml
  affinity:
    nodeAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          preference:
            matchExpressions:
              - key: kubernetes.io/hostname
                operator: In
                values:
                  - "ip-10-0-12-34.ec2.internal"
  ```
- Because the pod lands back on `Node A`, the volume is **already attached** at the hypervisor layer.
- Volume attachment completes in **`< 1.5 seconds`** rather than 90 seconds.
- If `Node A` was cordoned, drained, or terminated by an autoscaler, Kubernetes safely falls back to standard scheduling on another node.
