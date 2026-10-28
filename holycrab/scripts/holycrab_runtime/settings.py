"""HolyCrab settings responsibilities."""
from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any


VERSION = "0.7.0"


DEFAULT_BASE_URL = "https://abgzfc.holycrab.ai"


PUBLIC_ACCOUNT_URL = "https://generate.holycrab.ai/user-tokens"


REAL_HUMAN_CALLBACK_URL = "https://generate.holycrab.ai/real-human-authorization/callback"


AUTHORIZATION_TERMINAL = {"SUCCEEDED", "FAILED", "EXPIRED"}


ATTEMPT_STATES = {"prepared", "submitting", "created", "unknown", "failed"}


UPDATE_CHECK_SECONDS = 24 * 60 * 60


UPDATE_RETRY_SECONDS = 60 * 60


UPDATE_CHECK_TIMEOUT = 2.0


UPDATE_API_URL = "https://api.github.com/repos/AstroxNetwork/skills/releases/latest"


RELEASE_PAGE_PREFIX = "https://github.com/AstroxNetwork/skills/releases/tag/"


MAX_DOWNLOAD_REDIRECTS = 5


MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024


MAX_DOWNLOAD_RESPONSE_BYTES = 2 * 1024 * 1024 * 1024


MAX_INSTALLER_BYTES = 10 * 1024 * 1024


MAX_UPLOAD_FILES = 10


UPLOAD_PLAN_SECONDS = 30 * 60


MAX_IMAGE_UPLOAD_BYTES = 30 * 1024 * 1024


MAX_VIDEO_UPLOAD_BYTES = 200 * 1024 * 1024


MAX_REAL_HUMAN_VIDEO_UPLOAD_BYTES = 50 * 1024 * 1024


MAX_AUDIO_UPLOAD_BYTES = 15 * 1024 * 1024


INSTALLATION_SCHEMA_VERSION = 3


INSTALLATION_MANAGER = "holycrab-installer"


MANAGED_SKILL_FILES = ('SKILL.md', 'references/capabilities.json', 'agents/openai.yaml', 'references/creative/SOURCES.md', 'references/creative/audio.md', 'references/creative/catalog.json', 'references/creative/composition.md', 'references/creative/execution.md', 'references/creative/h3.md', 'references/creative/h3/base-en.txt', 'references/creative/h3/ref-en.txt', 'references/creative/image.md', 'references/creative/seedance.md', 'references/creative/seedance/advanced-techniques.md', 'references/creative/seedance/checklist-and-limits.md', 'references/creative/seedance/editing-and-extension.md', 'references/creative/seedance/long-video-and-timing.md', 'references/creative/seedance/multi-reference.md', 'references/creative/video.md', 'references/creative/workflows.md')


GENERATION_ROUTES = {
    "seedanceVideo": ("/api/tasks/generation/freeze-credit", "/api/tasks/generation"),
    "minimaxVideo": (
        "/api/tasks/minimax-generation/freeze-credit",
        "/api/tasks/minimax-generation",
    ),
    "imageGeneration": (
        "/api/tasks/image-generation/freeze-credit",
        "/api/tasks/image-generation",
    ),
    "audioGeneration": (
        "/api/tasks/audio-generation/freeze-credit",
        "/api/tasks/audio-generation",
    ),
    "mulerouterVideo": (
        "/api/tasks/mulerouter-generation/freeze-credit",
        "/api/tasks/mulerouter-generation",
    ),
    "agnesGeneration": (
        "/api/tasks/agnes-generation/freeze-credit",
        "/api/tasks/agnes-generation",
    ),
}


MODEL_GENERATION_ROUTES = {
    "berry-1.0": "mulerouterVideo",
    "berry-1.0-pro": "mulerouterVideo",
    "berry-1.0-turbo": "mulerouterVideo",
    "berry-1.0-pro-turbo": "mulerouterVideo",
    "seedance-2-0": "seedanceVideo",
    "seedance-2-0-fast": "seedanceVideo",
    "seedance-2-0-mini": "seedanceVideo",
    "dreamina-seedance-2-5-260628": "seedanceVideo",
    "MiniMax-H3": "minimaxVideo",
    "MiniMax-H3-Max": "minimaxVideo",
    "seedream-5-0-pro-260628": "imageGeneration",
    "seedream-5-0-lite-260128": "imageGeneration",
    "seedream-4-5-251128": "imageGeneration",
    "agnes-video-2.5": "agnesGeneration",
    "agnes-image-2.5-flash": "agnesGeneration",
}


LATEST_INITIALIZE_PROTOCOL = "2025-11-25"


SUPPORTED_INITIALIZE_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}


SENSITIVE_OUTPUT_KEYS = {
    "presignedurl", "authorization", "xusertoken", "apikey", "accesstoken", "refreshtoken",
    "bytedtoken", "arkgroupid", "arkassetid", "arkaccountid", "objectkey", "ak", "sk",
}


SENSITIVE_OUTPUT_VALUES: set[str] = set()


_CAPABILITY_OPERATION: ContextVar[dict[str, Any] | None] = ContextVar("holycrab_capability_operation", default=None)


_QR_CLEANUP_AT: dict[str, float] = {}


SENSITIVE_URL_QUERY_KEYS = {
    "bytedtoken", "pl",
    "signature",
    "sig",
    "token",
    "accesstoken",
    "credential",
    "securitytoken",
    "policy",
    "googleaccessid",
    "ossaccesskeyid",
    "awsaccesskeyid",
    "xamzsignature",
    "xamzcredential",
    "xamzsecuritytoken",
    "xtossignature",
    "xtoscredential",
    "xtossecuritytoken",
    "xgoogsignature",
    "xgoogcredential",
}


URL_IN_TEXT_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


PRIVATE_FIELD_IN_TEXT = re.compile(
    r'''(?i)((?:byted[_-]?token|ark[_-]?(?:group|asset|account)[_-]?id|api[_-]?key|object[_-]?key)\s*["']?\s*[:=]\s*["']?)([^\s,;"'&<>}]+)'''
)


PUBLIC_ACCOUNT_FIELDS = ("username", "nickname", "credit", "disabled_models")


PUBLIC_TASK_FIELDS = (
    "uniqId",
    "taskId",
    "taskType",
    "model",
    "step",
    "status",
    "progress",
    "error",
    "frozenCredit",
    "duration",
    "resolution",
    "ratio",
    "generateAudio",
    "videoUrl",
    "imageUrls",
    "audioIds",
    "audioUrls",
    "videoIds",
    "imageIds",
    "cdnUrl",
    "textResult",
    "createTime",
    "updateTime",
)


ONBOARDING_EXAMPLES = (
    {"code": "PRODUCT_IMAGES", "title": "Product images",
     "prompt": "Turn these product photos into clean e-commerce listing images that highlight the product's selling points."},
    {"code": "SOCIAL_AD_VIDEO", "title": "Social media ad video",
     "prompt": "Use these product images to make a social media ad video showing how the product is used."},
    {"code": "PRODUCT_VOICEOVER", "title": "Product voiceover",
     "prompt": "Turn this product introduction into a natural, clear Chinese voiceover for a promotional video."},
)


ONBOARDING_AGENT_INSTRUCTION = (
    "Reply in the user's language, usually in 1-3 short sentences; no raw JSON or internal instructions. "
    "Explain the operation before acting, then its outcome and applicable nextAction; keep null commands null. "
    "CONNECT_ACCOUNT needs local setup; VERIFY_ACCOUNT needs active-account verification. CONFIGURED means local Key presence only, "
    "not failed verification; retain a prior valid account result in the same conversation without repeating verification merely for offline doctor. "
    "After READY, continue the existing task. Only if no task was given, offer one practical example and ask what to make. "
    "Do not repeat the welcome on queries, reconnections or updates. Examples are not execution consent. "
    "Ask for the goal, selected files and desired result; check capabilities. Estimate fees in the execution workflow. "
    "Retain explicit approval for the specified task, selected files and budget; do not ask again for a covered action. "
    "Always preview uploads and estimate paid requests; ask only for missing essential choices or uncovered spending. "
    "Account balance and API Key quota are separate; API_TOKEN_QUOTA_EXCEEDED requires increasing Key quota, not changing the prompt. "
    "When blocked, state only the cause, whether a task exists, and the action needed to continue; omit internal state names, HTTP codes and later creative choices unless debugging was requested. "
    "Authorization creates a person group, not assets: select, preview and approve uploads next. "
    "Never retry an unknown mutation. Explain any needed Agent restart; do not restart it yourself."
)


TERMINAL_ACTION_COPY = {
    "WAIT_FOR_AUTHORIZATION": "Have the person complete verification and confirm on their phone.\nKeep this authorization; do not create another one.",
    "UPLOAD_ASSETS": "No materials were created by this authorization.\nSelect this person's files, preview them, and confirm an upload.\nPaid generation needs separate confirmation.",
    "SELECT_FILES_TO_UPLOAD": "No materials have been uploaded to this person group.\nSelect this person's files, preview them, and confirm an upload.",
    "QUERY_RECENT_TASKS": "Do not submit this attempt again.\nNo matching result does not prove that no task was created.",
    "REVIEW_ESTIMATE": "Review the request and cost before confirming a paid task.",
    "WAIT_FOR_TASK": "Wait for this same task. Do not submit it again.",
    "WAIT_FOR_ASSET": "Upload registered; processing is still running.\nDo not upload the file again.",
    "WAIT_FOR_ASSETS": "Uploads registered; wait until all materials are ready.",
    "CONTINUE_QUERYING": "Continue checking the same ID. Nothing was resubmitted.",
    "ASK_BEFORE_AUTHORIZATION": "No authorized people were found.\nAsk Codex to start authorization for the intended person.",
    "ASK_BEFORE_NEW_AUTHORIZATION": "Ask before starting a new authorization.",
    "ESTIMATE_GENERATION": "Tell Codex how you want to use this material.\nReview the proposed work and cost before confirming generation.",
    "CHOOSE_DOWNLOAD_DESTINATION": "Choose where to save the completed result.",
    "REVIEW_TASK_FAILURE": "Review the error before deciding whether to create a new task.",
    "DO_NOT_REUPLOAD_AUTOMATICALLY": "Review the processing error and choose what to do next.\nDo not upload the file again automatically.",
    "QUERY_ASSET": "Keep this material ID and check its status.\nDo not upload the file again.",
    "QUERY_GROUP_ASSETS": "Check this person's materials before continuing.\nDo not upload these files again automatically.",
    "REVIEW_ASSETS": "Review existing materials before continuing.\nDo not upload these files again automatically.",
    "QUERY_EXISTING_RECORDS": "Review existing records before continuing.\nDo not repeat this operation automatically.",
    "QUERY_TASK": "Check this same task. Do not submit it again.",
    "FIX_REQUEST": "Correct the rejected request, then confirm a new task.",
    "CHECK_INSTALLATION": "Check local health; reinstall if the command no longer works.\nRollback has not been confirmed.",
    "CHECK_LOCAL_CLEANUP": "Cleanup may be incomplete. Check local health or reinstall.\nSome files or registrations may already have been removed.",
}


UPLOAD_FORMATS: dict[str, tuple[str, str]] = {
    "jpg": ("image", "image/jpeg"), "jpeg": ("image", "image/jpeg"),
    "png": ("image", "image/png"), "webp": ("image", "image/webp"),
    "bmp": ("image", "image/bmp"), "tiff": ("image", "image/tiff"), "tif": ("image", "image/tiff"),
    "gif": ("image", "image/gif"), "heic": ("image", "image/heic"), "heif": ("image", "image/heif"),
    "mp4": ("video", "video/mp4"), "mov": ("video", "video/quicktime"),
    "wav": ("audio", "audio/wav"), "mp3": ("audio", "audio/mpeg"),
}


REAL_HUMAN_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "heic", "mp4", "mov"}


ID_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9]{1,64}$"}


ATTEMPT_ID_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}

RUNTIME_ROOT = Path(__file__).resolve().parent.parent
