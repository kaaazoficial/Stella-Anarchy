from stella.ai_core import StellaAICore


def test_shared_ai_prompt_requires_semantic_insult_detection():
    source = __import__("inspect").getsource(StellaAICore)
    assert "DETECCIÓN SEMÁNTICA DE INSULTOS" in source
    assert "regionalismos" in source
    assert "ataques dirigidos a Stella" in source
