"""Internal pause/resume lineage, separate from provisional winner protection."""

from dataclasses import asdict, dataclass
from hashlib import sha256

from shipos.contracts import GuestManifest
from shipos.sim.log import canonical


def manifest_hash(manifest: GuestManifest) -> str:
    return sha256(canonical(manifest.model_dump(mode="json"))).hexdigest()


@dataclass(frozen=True)
class ResumeApproval:
    """Created only after a chosen pause/suspend applies, then bound to its task IDs."""

    origin_task: int
    guest: str
    activity: str
    mode: str
    hold_mode: str
    hold_entered_s: float
    not_before_s: float
    elapsed_s: float
    manifest_sha256: str

    def record(self) -> dict:
        return {"version": 1, **asdict(self)}

    def blocker(self, task, data, manifest: GuestManifest, entered) -> str | None:
        if (
            task.get("guest") != self.guest
            or task.get("mode") != self.mode
            or task.get("activity") != self.activity
            or task.get("elapsed_s") != self.elapsed_s
        ):
            return "approved_resume_payload_changed"
        if manifest_hash(manifest) != self.manifest_sha256:
            return "approved_resume_manifest_changed"
        if data["t_s"] < self.not_before_s:
            return "approved_resume_not_due"
        runtime = data["guests"][self.guest]
        if (
            runtime["mode"] != self.hold_mode
            or runtime["activity"] is not None
            or entered[self.guest] != self.hold_entered_s
        ):
            return "approved_resume_episode_changed"
        return None
