"""userconf.txt (pi's password) configuration endpoints."""

import subprocess

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from app.configs.userconf import UserconfConfig
from app.routers.base import BaseConfigRouter
from app.utils.subprocess_async import run_subprocess_async

# Create the router using the base class - handles both tracker mode
# (privileged write to /boot/firmware) and server mode (versioned config
# group directory) generically, same as schedule.py.
userconf_router = BaseConfigRouter(UserconfConfig, "userconf", "userconf")
router = userconf_router.router


class UserconfUpdate(BaseModel):
    content: str = Field(..., description="userconf.txt content, e.g. 'pi:$6$...' (see openssl passwd -6)")


class UserconfPasswordHash(BaseModel):
    password: str = Field(..., min_length=1, max_length=1024)


# Override methods to use our specific model
@router.put("")
async def update_userconf(
    config: UserconfUpdate,
    config_group: str | None = Query(None, description="Config group name for server mode"),
):
    config_dict = config.model_dump()
    return await userconf_router.update_config_helper(config_dict, config_group)


@router.post("/validate")
async def validate_userconf(
    config: UserconfUpdate,
    config_group: str | None = Query(None, description="Config group name for server mode"),
):
    config_dict = config.model_dump()
    return userconf_router.validate_config_helper(config_dict, config_group)


@router.post("/hash")
async def hash_userconf_password(payload: UserconfPasswordHash):
    """Hash a plaintext password into a 'pi:$6$...' userconf.txt line.

    A convenience for operators without their own way to run
    `openssl passwd -6` - this never writes anything or touches a live
    account, it only shells out to openssl (same tool/convention used
    everywhere else in this repo for password hashes) and returns the
    result for the caller to then PUT to this same router. The password is
    piped via stdin, never passed as a command-line argument, so it never
    appears in a process listing (`ps`).
    """
    try:
        result = await run_subprocess_async(
            ["openssl", "passwd", "-6", "-stdin"],
            input_data=(payload.password + "\n").encode(),
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise HTTPException(status_code=500, detail=f"Failed to hash password: {e}")

    return {"content": f"pi:{result.stdout.strip()}"}
