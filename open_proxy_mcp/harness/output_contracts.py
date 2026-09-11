"""도메인 스키마를 모델의 Structured Outputs 계약으로 변환한다."""
from __future__ import annotations

from copy import deepcopy

from open_proxy_mcp.services.election_structure import (
    DATA_TYPES, StructureAssessment, StructureFact, StructureGap,
    StructureFinding, StructureJudgment,
)
from open_proxy_mcp.services.structure_protocol import AgendaReview


class OutputContractError(ValueError):
    pass


def _inline(schema: dict) -> dict:
    definitions = schema.get('$defs', {})
    def visit(node, ancestors=()):
        if isinstance(node, list):
            return [visit(x, ancestors) for x in node]
        if not isinstance(node, dict):
            return node
        if '$ref' in node:
            ref = node['$ref']
            name = ref.removeprefix('#/$defs/')
            if not ref.startswith('#/$defs/') or name not in definitions or name in ancestors:
                raise OutputContractError('unsupported_output_reference')
            return visit({**definitions[name], **{k:v for k,v in node.items() if k != '$ref'}}, (*ancestors, name))
        return {k: visit(v, ancestors) for k,v in node.items() if k != '$defs'}
    return visit(deepcopy(schema))


def wire_schema(schema: dict) -> dict:
    """도메인 제약을 보존하면서 모델 출력용 스키마로 변환한다."""
    # 형식을 제한해도 누락된 사실을 채우지는 않는다. null·빈 배열·gap 계약을 유지한다.
    def close(node):
        if isinstance(node, list):
            return [close(x) for x in node]
        if not isinstance(node, dict):
            return node
        if not node:
            raise OutputContractError('untyped_output_schema')
        properties = node.get('properties', {})
        if 'allOf' in node:
            # 정관 사건의 if/then 제약은 API가 지원하는 enum별 anyOf로 풀어 쓴다.
            # 제약을 삭제하면 proposal에 가결 결과가 붙을 수 있으므로 삭제는 금지한다.
            return {'anyOf': [close(x) for x in _enum_branches(node)]}
        if ({'fact_id', 'kind', 'data', 'evidence_refs'} <= properties.keys()
            and properties['data'].get('additionalProperties') is True):
            # 서버가 받는 유연한 fact.data를 모델 생성 시에만 kind별 명시적 스키마로 펼친다.
            variants = []
            for kind, model in DATA_TYPES.items():
                variant = deepcopy(node)
                variant['properties']['kind'] = {'type': 'string', 'enum': [kind]}
                variant['properties']['data'] = _inline(model.model_json_schema())
                # 재귀적으로 fact variant를 또 만들지 않도록 일반 object 경로를 사용한다.
                variants.append(close_object(variant))
            return {'anyOf': variants}
        return close_object(node)

    def close_object(node):
        result = {}
        for key, value in node.items():
            if key in {'title', 'default', 'examples'}:
                continue
            if key == 'properties':
                result[key] = {name: close(child) for name, child in value.items()}
            elif key in {'items', 'anyOf', 'oneOf', 'allOf'}:
                if key in {'oneOf', 'allOf'}:
                    raise OutputContractError('unsupported_output_composition')
                result[key] = close(value)
            elif key == 'const':
                result['enum'] = [value]
            else:
                result[key] = deepcopy(value)
        if result.get('type') == 'object':
            if 'properties' not in result or result.get('additionalProperties', False) is not False:
                # 구조를 모르는 dict를 빈 object로 바꾸면 정보가 사라지므로 변환을 거부한다.
                raise OutputContractError('open_output_object')
            result['additionalProperties'] = False
            result['required'] = list(result['properties'])
        return result

    value = close(_inline(schema))
    if value.get('type') != 'object' or 'anyOf' in value:
        raise OutputContractError('output_root_must_be_object')
    return value


def _enum_branches(node: dict) -> list[dict]:
    conditions = node['allOf']
    discriminators = set()
    for condition in conditions:
        if set(condition) != {'if', 'then'} or set(condition['then']) - {'properties', 'required'}:
            raise OutputContractError('unsupported_output_condition')
        selector = condition['if'].get('properties', {})
        if (len(selector) != 1 or set(condition['if']) - {'properties', 'required'}
            or condition['if'].get('required') != list(selector)
            or any(set(value) - {'enum', 'const'} for value in selector.values())
            or not set(condition['then'].get('required', [])) <= node.get('properties', {}).keys()):
            raise OutputContractError('unsupported_output_condition')
        discriminators.update(selector)
    if len(discriminators) != 1:
        raise OutputContractError('unsupported_output_condition')
    discriminator = next(iter(discriminators))
    choices = node.get('properties', {}).get(discriminator, {}).get('enum')
    if not choices:
        raise OutputContractError('unsupported_output_condition')
    variants = []
    for choice in choices:
        variant = deepcopy(node)
        variant.pop('allOf')
        variant['properties'][discriminator]['enum'] = [choice]
        for condition in conditions:
            selector = condition['if']['properties'][discriminator]
            allowed = selector.get('enum', [selector.get('const')])
            if choice not in allowed:
                continue
            for name, constraint in condition['then'].get('properties', {}).items():
                original = variant['properties'][name]
                if 'anyOf' in original and constraint.get('type'):
                    candidates = [x for x in original['anyOf'] if x.get('type') == constraint['type']]
                    if len(candidates) != 1:
                        raise OutputContractError('unsupported_output_condition')
                    original = {**{k:v for k,v in original.items() if k != 'anyOf'}, **candidates[0]}
                variant['properties'][name] = {**original, **constraint}
        variants.append(variant)
    return variants


def assessment_output_schema() -> dict:
    # MCP는 잘못된 개별 행도 받아 격리한다. 모델 생성 계약은 그보다 구체적으로 제공한다.
    schema = _inline(StructureAssessment.model_json_schema())
    for field, model in {'facts': StructureFact, 'gaps': StructureGap,
                         'findings': StructureFinding, 'judgments': StructureJudgment,
                         'reviews': AgendaReview}.items():
        if field in schema['properties']:
            schema['properties'][field]['items'] = _inline(model.model_json_schema())
    return wire_schema(schema)


def response_format(schema: dict, mode: str) -> dict:
    if mode == 'json_object':
        return {'type': 'json_object'}
    if mode != 'json_schema':
        raise OutputContractError('unsupported_output_mode')
    return {'type': 'json_schema', 'name': 'opm_assessment', 'strict': True,
            'schema': wire_schema(schema)}
