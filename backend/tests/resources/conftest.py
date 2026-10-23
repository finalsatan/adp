"""资源模块测试夹具：注册只用于测试的资源类型 test_res。"""

from collections.abc import Iterator

import pytest

from app.resources.registry import ResourceType, register_resource_type, unregister_resource_type

TEST_TYPE = ResourceType(code="test_res", label="测试资源")


@pytest.fixture
def test_res_type() -> Iterator[ResourceType]:
    """注册测试类型，测试结束时注销。"""
    register_resource_type(TEST_TYPE)
    yield TEST_TYPE
    unregister_resource_type(TEST_TYPE.code)
