from analysis_studio.codegen import generate_loader_cpp
from analysis_studio.model import Graph
from analysis_studio.registry import NODE_SPECS


def add(graph, node_type, title=None, **properties):
    node = graph.add_node(NODE_SPECS[node_type], 0, 0, title)
    node.properties.update(properties)
    return node


def connect(graph, first, second):
    graph.add_edge(first.id, "out", second.id, "in")


def test_codegen_infers_loader_and_uses_visible_root_order():
    graph = Graph("program", "Two loaders", "loader")
    first = add(
        graph, "loader_decl", "Signal Loader", variable_name="signal_loader", branch="signal_tree"
    )
    first_load = add(
        graph, "load", directory_cpp="argv[1]", including_cpp="argv[2]", label="SIGNAL"
    )
    first_end = add(graph, "loader_end", "End signal")
    connect(graph, first, first_load)
    connect(graph, first_load, first_end)

    second = add(
        graph, "loader_decl", "Fit Loader", variable_name="fit_loader", branch="fit_tree"
    )
    cut = add(graph, "cut", "Cut before Load", condition="M > 1.7")
    load = add(
        graph,
        "load_with_cut",
        directory_cpp="argv[3]",
        including_cpp='".root"',
        label="DATA",
        condition="deltaE < 0.2",
    )
    second_end = add(graph, "loader_end", "End fit")
    connect(graph, second, cut)
    connect(graph, cut, load)
    connect(graph, load, second_end)

    graph.set_root_order(second.id, 1)
    code = generate_loader_cpp(graph)
    assert code.index('Loader fit_loader("fit_tree");') < code.index(
        'Loader signal_loader("signal_tree");'
    )
    assert code.index("fit_loader.Cut") < code.index("fit_loader.LoadWithCut")
    assert code.count(".end();") == 2
    assert "loader_ref" not in code
