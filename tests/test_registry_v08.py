from analysis_studio.registry import NODE_SPECS, foreach_properties_for_mode, node_property_visible


def property_names(node_type: str) -> list[str]:
    return [prop.name for prop in NODE_SPECS[node_type].properties]


def test_execution_blocks_only_keep_per_block_lsf_queue():
    loader = property_names("loader_execute")
    custom = property_names("custom_command")
    for names in (loader, custom):
        assert "lsf_queue" in names
        assert "local_max_parallel" not in names
        assert "lsf_max_inflight" not in names
        assert "lsf_extra_options" not in names


def test_custom_command_build_properties_match_v8_design():
    spec = NODE_SPECS["custom_command"]
    names = [prop.name for prop in spec.properties]
    build_mode = next(prop for prop in spec.properties if prop.name == "build_mode")
    assert build_mode.choices == ("auto", "custom")
    assert "output_name" not in names
    assert "use_analysis_framework" not in names
    for name in ("compile_command", "additional_sources", "compile_flags", "link_flags"):
        assert name in names


def test_for_each_has_no_parallel_limit_property():
    for mode in ("root_files", "csv_rows", "values"):
        assert "max_parallel" not in [prop.name for prop in foreach_properties_for_mode(mode)]


def test_property_visibility_depends_on_backend_and_build_mode():
    assert not node_property_visible("loader_execute", "lsf_queue", "local", {})
    assert node_property_visible("loader_execute", "lsf_queue", "lsf", {})
    assert not node_property_visible(
        "custom_command", "compile_command", "local", {"build_mode": "auto"}
    )
    assert node_property_visible(
        "custom_command", "compile_command", "local", {"build_mode": "custom"}
    )
