"""The masking corpus: no planted secret may survive, in any of the samples.

Corpus files are templates: ``${NAME}`` is replaced by a fake secret from
``fakes.py`` before masking. YAML samples are masked as objects, log samples as
text. This test is mandatory in CI (task 3.2).
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ape.sanitize import DETECTORS, MaskedPayload, Masker, derive_tenant_key

from .fakes import FAKES, NEEDLES

CORPUS = Path(__file__).parent / "corpus"
SAMPLES = sorted(CORPUS.iterdir())
VARIABLE = re.compile(r"\$\{([A-Z_]+)\}")
KEY = derive_tenant_key(b"corpus-master-key-0123456789abcdef", "org_corpus")


def render(path: Path) -> tuple[str, set[str]]:
    template = path.read_text()
    used = set(VARIABLE.findall(template))

    def substitute(match: re.Match[str]) -> str:
        value = FAKES[match.group(1)]
        if "\n" in value or value.startswith("{"):
            # Keep multi-line and JSON values valid inside YAML block and quoted scalars.
            indent = " " * (match.start() - template.rfind("\n", 0, match.start()) - 1)
            return value.replace("\n", "\n" + indent) if path.suffix == ".yaml" else value
        return value

    return VARIABLE.sub(substitute, template), used


def mask_sample(path: Path, masker: Masker) -> str:
    text, _ = render(path)
    if path.suffix == ".yaml":
        documents: list[Any] = list(yaml.safe_load_all(text))
        return json.dumps(masker.mask(documents), ensure_ascii=False)
    return masker.mask_text(text)


def test_corpus_is_large_enough_and_uses_every_fake() -> None:
    assert len(SAMPLES) >= 50
    assert {p.suffix for p in SAMPLES} == {".yaml", ".log"}
    used: set[str] = set()
    for path in SAMPLES:
        names = set(VARIABLE.findall(path.read_text()))
        assert names, f"{path.name} plants no secret"
        assert names <= set(FAKES), f"{path.name} uses an unknown variable"
        used |= names
    assert used == set(FAKES)


def test_templates_contain_no_literal_secrets() -> None:
    # The repository itself must stay clean: secrets exist only at test time.
    for path in SAMPLES:
        template = path.read_text()
        for values in NEEDLES.values():
            for needle in values:
                assert needle not in template


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_no_planted_secret_survives(path: Path) -> None:
    rendered, used = render(path)
    for name in used:  # the sample really contains what it claims to plant
        for needle in NEEDLES[name]:
            assert needle in rendered.replace("\\n", "\n") or needle in rendered
    masker = Masker(KEY)
    output = mask_sample(path, masker)
    leaked = [name for name in used for needle in NEEDLES[name] if needle in output]
    assert not leaked, f"{path.name} leaked {leaked}: {output[:400]}"
    assert sum(masker.redactions.values()) >= 1
    assert set(masker.redactions) <= set(DETECTORS)


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_masking_is_deterministic_and_idempotent(path: Path) -> None:
    first = mask_sample(path, Masker(KEY))
    assert mask_sample(path, Masker(KEY)) == first
    again = Masker(KEY)
    assert again.mask_text(first) == first if path.suffix == ".log" else True
    if path.suffix == ".yaml":
        assert json.dumps(again.mask(json.loads(first)), ensure_ascii=False) == first
    assert sum(again.redactions.values()) == 0


def test_every_detector_family_fires_somewhere_in_the_corpus() -> None:
    masker = Masker(KEY)
    for path in SAMPLES:
        mask_sample(path, masker)
    assert set(masker.redactions) == set(DETECTORS)
    assert all(count > 0 for count in masker.redactions.values())


def test_structure_survives_masking() -> None:
    masker = Masker(KEY)
    documents = json.loads(mask_sample(CORPUS / "13-connection-strings.yaml", masker))
    env = documents[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    assert env[0]["value"].startswith("mongodb://reader:<secret:url_password:")
    assert env[0]["value"].endswith("@mongo-0.mongo:27017/app")
    assert env[2]["value"].startswith("redis://:<secret:url_password:")
    assert (
        documents[0]["spec"]["template"]["spec"]["containers"][0]["image"] == "acme/consumer:5.2.1"
    )
    assert documents[0]["metadata"] == {"name": "consumer", "namespace": "prod"}


def test_public_ips_are_masked_only_on_request() -> None:
    line = "peer 8.8.4.4 connected through 10.0.0.7 and 127.0.0.1"
    assert Masker(KEY).mask_text(line) == line
    masked = Masker(KEY, mask_public_ips=True).mask_text(line)
    assert "8.8.4.4" not in masked
    assert "10.0.0.7" in masked and "127.0.0.1" in masked


def test_identifiers_are_not_mistaken_for_secrets() -> None:
    obj = {
        "metadata": {
            "name": "kube-controller-manager-ape-dev-control-plane",
            "uid": "0b9f2c1e-7d1a-4c53-9f0e-2a6b8d4c1e77",
            "resourceVersion": "184467",
            "labels": {"pod-template-hash": "7f9c4d5b6a", "app.kubernetes.io/name": "storefront"},
        },
        "spec": {
            "nodeName": "ip-10-0-12-34.eu-central-1.compute.internal",
            "containers": [
                {
                    "name": "web",
                    "image": "ghcr.io/acme/StoreFront9@sha256:" + "ab12" * 16,
                    "args": ["--feature-gates=KubeletInUserNamespace=true", "--v=2"],
                }
            ],
        },
        "status": {
            "containerStatuses": [{"imageID": "docker.io/library/nginx@sha256:" + "0f" * 32}]
        },
    }
    masker = Masker(KEY)
    assert masker.mask(obj) == obj
    assert sum(masker.redactions.values()) == 0


def test_masked_payload_can_only_come_from_the_masker() -> None:
    payload = Masker(KEY).mask_payload({"password": "x"})
    assert isinstance(payload, MaskedPayload)
    assert payload.data["password"].startswith("<secret:key_name:")
    with pytest.raises(TypeError):
        MaskedPayload({"password": "x"}, object())
