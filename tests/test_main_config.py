import main


def test_build_llm_config_defaults_to_prompt_yaml(tmp_path):
    config_path = tmp_path / "config.yaml"
    prompt_path = tmp_path / "prompt.yaml"
    config_path.write_text("llm:\n  model: test\n", encoding="utf-8")
    prompt_path.write_text("prompt: default prompt text\n", encoding="utf-8")

    cfg = {"llm": {"model": "test"}}

    llm_cfg = main._build_llm_config(cfg, str(config_path))

    assert llm_cfg["prompt"] == "default prompt text"


def test_build_llm_config_keeps_inline_prompt(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  prompt: inline prompt\n", encoding="utf-8")

    cfg = {"llm": {"prompt": "inline prompt"}}

    llm_cfg = main._build_llm_config(cfg, str(config_path))

    assert llm_cfg["prompt"] == "inline prompt"
