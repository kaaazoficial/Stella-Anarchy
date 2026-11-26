import inspect

import stella.ai_core as ai_core


def test_shared_ai_prompt_requires_semantic_insult_detection():
    source = inspect.getsource(ai_core)
    assert "DETECCIÓN SEMÁNTICA DE INSULTOS" in source
    assert "regionalismos" in source
    assert "ataques dirigidos a Stella" in source
