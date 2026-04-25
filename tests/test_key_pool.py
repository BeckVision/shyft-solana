import os

import pytest

from shyft_solana import KeyPool, resolve_api_keys


def test_resolve_api_keys_merges_and_dedupes(monkeypatch):
    monkeypatch.setenv("SHYFT_API_KEYS", "env1,env2")
    keys = resolve_api_keys(api_key="key1", api_keys=["key2", "key1"])
    assert keys == ["key1", "key2"]


def test_resolve_api_keys_uses_environment(monkeypatch):
    monkeypatch.setenv("SHYFT_API_KEYS", "env1, env2")
    monkeypatch.setenv("SHYFT_API_KEY", "env1")
    assert resolve_api_keys() == ["env1", "env2"]


def test_key_pool_requires_key():
    with pytest.raises(ValueError):
        KeyPool([])


def test_batch_rpc_rotates_keys():
    pool = KeyPool(["k1", "k2"])
    assert pool.get_key_for_batch_rpc() == "k1"
    assert pool.get_key_for_batch_rpc() == "k2"
    assert pool.get_key_for_batch_rpc() == "k1"
    assert [row["requests"] for row in pool.stats()] == [2, 1]


def test_report_success_resets_429_counter():
    pool = KeyPool(["k1"])
    pool.report_429("k1")
    assert pool.stats()[0]["consecutive_429s"] == 1
    pool.report_success("k1")
    assert pool.stats()[0]["consecutive_429s"] == 0
