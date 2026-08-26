"""Platform adapter implementations."""

from surveillance_video_agent.adapters.base import (
    BasePlatformAdapter,
    CommandResult,
    CommandRunner,
    PlatformAdapter,
    SubprocessCommandRunner,
)
from surveillance_video_agent.adapters.dailymotion import DailymotionAdapter
from surveillance_video_agent.adapters.peertube import PeerTubeAdapter
from surveillance_video_agent.adapters.youtube import YouTubeAdapter

__all__ = [
    "BasePlatformAdapter",
    "CommandResult",
    "CommandRunner",
    "PlatformAdapter",
    "SubprocessCommandRunner",
    "DailymotionAdapter",
    "PeerTubeAdapter",
    "YouTubeAdapter",
]
