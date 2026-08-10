"""Generic synchronous runtime for plugin-owned artifact actions."""
from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lazyllm.tools.tool_config_inject import inject_tool_config
from lazymind.chat.plugin import plugin_loader
from lazymind.model_config import inject_model_config

router = APIRouter()
logger = logging.getLogger(__name__)


class PluginActionInvokeRequest(BaseModel):
    plugin_id: str
    action: str
    phase: Literal['preview', 'execute']
    slot: str
    artifact: Any = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    artifact_store: str = ''
    llm_config: Optional[Dict[str, Any]] = None
    tool_config: Optional[Dict[str, Any]] = None


def _action_definition(plugin_id: str, action_id: str) -> tuple[Any, Dict[str, Any]]:
    spec = plugin_loader.get_plugin(plugin_id)
    if spec is None:
        raise HTTPException(status_code=404, detail='plugin not found')
    actions = spec.yaml.get('artifact_actions') or {}
    action = actions.get(action_id) if isinstance(actions, dict) else None
    if not isinstance(action, dict):
        raise HTTPException(status_code=404, detail='artifact action not found')
    return spec, action


@router.post('/api/plugin/actions:invoke', summary='Invoke a plugin-owned artifact action')
def invoke_plugin_action(request: PluginActionInvokeRequest) -> Dict[str, Any]:
    spec, definition = _action_definition(request.plugin_id, request.action)
    if request.slot not in (definition.get('slots') or []):
        raise HTTPException(status_code=400, detail='action is not enabled for this slot')
    tool_name = str(definition.get(f'{request.phase}_tool') or '')
    tool = spec.get_script_tool(tool_name) if tool_name else None
    if tool is None:
        raise HTTPException(status_code=500, detail='artifact action tool is unavailable')

    kwargs = dict(request.arguments)
    reserved = {'artifact', 'artifact_store', 'slot'} & kwargs.keys()
    if reserved:
        raise HTTPException(status_code=400, detail=f'reserved arguments: {sorted(reserved)}')
    parameters = inspect.signature(tool).parameters
    if 'artifact' in parameters:
        kwargs['artifact'] = request.artifact
    if 'artifact_store' in parameters:
        kwargs['artifact_store'] = request.artifact_store
    if 'slot' in parameters:
        kwargs['slot'] = request.slot
    try:
        inject_model_config(request.llm_config or {})
        inject_tool_config(request.tool_config or {})
        return {'result': tool(**kwargs)}
    except ValueError as exc:
        code = str(getattr(exc, 'error_code', 'PLUGIN_ACTION_INVALID'))
        detail: Dict[str, Any] = {'code': code, 'message': str(exc)}
        detail.update(getattr(exc, 'details', {}) or {})
        status = 409 if code in {
            'SELECTION_AMBIGUOUS', 'SELECTION_STALE',
        } else 422
        raise HTTPException(status_code=status, detail=detail) from exc
    except TypeError as exc:
        raise HTTPException(
            status_code=422,
            detail={'code': 'PLUGIN_ACTION_INVALID', 'message': str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception(
            'Plugin artifact action failed: plugin=%s action=%s phase=%s',
            request.plugin_id, request.action, request.phase,
        )
        raise HTTPException(
            status_code=502,
            detail={'code': 'PLUGIN_ACTION_FAILED', 'message': str(exc)},
        ) from exc
