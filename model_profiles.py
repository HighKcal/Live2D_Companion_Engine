"""Validated model profiles and stable Live2D asset resolution."""
from copy import deepcopy
import json
from pathlib import Path


class ProfileError(ValueError):
    pass


def _require(mapping, key, expected, context):
    value = mapping.get(key)
    if not isinstance(value, expected):
        names = expected.__name__ if isinstance(expected, type) else '/'.join(t.__name__ for t in expected)
        raise ProfileError(f'{context}.{key} must be {names}')
    return value


class ModelProfile:
    def __init__(self, path, root):
        self.path = Path(path).resolve()
        self.root = Path(root).resolve()
        try:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise ProfileError(f'{self.path.name}: cannot read profile: {error}') from error
        if not isinstance(self.data, dict):
            raise ProfileError(f'{self.path.name}: profile root must be an object')
        if self.data.get('schema_version') != 1:
            raise ProfileError(f'{self.path.name}: unsupported schema_version')
        self.id = _require(self.data, 'id', str, self.path.name)
        self.display_name = _require(self.data, 'display_name', str, self.path.name)
        model = _require(self.data, 'model', dict, self.path.name)
        relative = Path(_require(model, 'path', str, f'{self.path.name}.model'))
        self.model_path = (self.root / relative).resolve()
        if not self.model_path.is_relative_to(self.root):
            raise ProfileError(f'{self.path.name}: model path escapes project root')
        if not self.model_path.is_file():
            raise ProfileError(f'{self.path.name}: model does not exist: {relative}')
        assets_path = self.model_path.parent / 'assets.json'
        try:
            self.assets = json.loads(assets_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise ProfileError(f'{self.path.name}: invalid runtime assets.json: {error}') from error
        if not isinstance(self.assets, dict):
            raise ProfileError(f'{self.path.name}: runtime assets.json root must be an object')
        expression_items = self.assets.get('expressions', [])
        motion_items = self.assets.get('motions', [])
        if not isinstance(expression_items, list) or not isinstance(motion_items, list):
            raise ProfileError(f'{self.path.name}: runtime asset collections must be lists')
        for item in expression_items + motion_items:
            if not isinstance(item, dict) or not isinstance(item.get('file'), str):
                raise ProfileError(f'{self.path.name}: runtime asset needs a file')
        self.expressions = {self.asset_identifier(item): item for item in expression_items}
        self.motions = {self.asset_identifier(item): item for item in motion_items}
        if len(self.expressions) != len(expression_items) or len(self.motions) != len(motion_items):
            raise ProfileError(f'{self.path.name}: duplicate asset identifier')
        self.parameter_ids = _require(self.data, 'parameters', dict, self.path.name)
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in self.parameter_ids.items()):
            raise ProfileError(f'{self.path.name}.parameters must map semantic names to parameter IDs')
        self.presentation = _require(self.data, 'presentation', dict, self.path.name)
        self.hit_areas = _require(self.data, 'hit_areas', dict, self.path.name)
        self.expression_actions = _require(self.data, 'expressions', dict, self.path.name)
        self.motion_actions = self.data.get('motions', {})
        if not isinstance(self.motion_actions, dict):
            raise ProfileError(f'{self.path.name}.motions must be an object')
        self.behavior = _require(self.data, 'behavior', dict, self.path.name)
        self.controls = self.data.get('controls', [])
        if not isinstance(self.controls, list):
            raise ProfileError(f'{self.path.name}.controls must be a list')
        self._validate_assets()

    def _validate_assets(self):
        for collection in (self.expressions, self.motions):
            for identifier, item in collection.items():
                asset_path = (self.model_path.parent / item['file']).resolve()
                if not asset_path.is_relative_to(self.model_path.parent) or not asset_path.is_file():
                    raise ProfileError(f'{self.path.name}: runtime asset missing for {identifier}')
        for semantic in ('ambient', 'negative', 'positive'):
            candidates = self.expression_actions.get(semantic, [])
            if not isinstance(candidates, list):
                raise ProfileError(f'{self.path.name}.expressions.{semantic} must be a list')
            for item in candidates:
                if not isinstance(item, dict) or not isinstance(item.get('asset'), str):
                    raise ProfileError(f'{self.path.name}.expressions.{semantic} needs an asset')
                if item['asset'] not in self.expressions:
                    raise ProfileError(f'{self.path.name}: unknown expression asset {item["asset"]}')
                values = item.get('parameter_values', {})
                if not isinstance(values, dict):
                    raise ProfileError(f'{self.path.name}.expressions.{semantic} parameter_values must be an object')
                if any(key not in self.parameter_ids for key in values):
                    raise ProfileError(f'{self.path.name}.expressions.{semantic} references unknown semantic parameter')
        for semantic, item in self.motion_actions.items():
            if not isinstance(item, dict) or not isinstance(item.get('asset'), str):
                raise ProfileError(f'{self.path.name}.motions.{semantic} needs an asset')
            if item['asset'] not in self.motions:
                raise ProfileError(f'{self.path.name}: unknown motion asset {item["asset"]}')
        head = self.hit_areas.get('head')
        if not isinstance(head, dict) or head.get('type') != 'rect':
            raise ProfileError(f'{self.path.name}.hit_areas.head must be a rect')
        for name, area in self.hit_areas.items():
            if not isinstance(area, dict) or area.get('type') != 'rect':
                raise ProfileError(f'{self.path.name}.hit_areas.{name} must be a rect')
            rect = area.get('rect')
            if (not isinstance(rect, list) or len(rect) != 4 or
                    not all(isinstance(v, (int, float)) for v in rect) or
                    rect[0] >= rect[2] or rect[1] >= rect[3]):
                raise ProfileError(
                    f'{self.path.name}.hit_areas.{name}.rect must contain ordered left, bottom, right, top numbers')
        for key in ('scale', 'window_aspect', 'window_height_per_character_height'):
            if not isinstance(self.presentation.get(key), (int, float)):
                raise ProfileError(f'{self.path.name}.presentation.{key} must be numeric')
        window_scale = self.presentation.get('render_window_scale', 1.0)
        if not isinstance(window_scale, (int, float)) or window_scale <= 0:
            raise ProfileError(f'{self.path.name}.presentation.render_window_scale must be positive')
        bottom_offscreen = self.presentation.get('bottom_offscreen_fraction', 0.65)
        if (not isinstance(bottom_offscreen, (int, float)) or
                not 0 <= bottom_offscreen <= 1):
            raise ProfileError(
                f'{self.path.name}.presentation.bottom_offscreen_fraction must be between 0 and 1')
        for item in self.controls:
            if not isinstance(item, dict) or item.get('parameter') not in self.parameter_ids:
                raise ProfileError(f'{self.path.name}: control references unknown semantic parameter')
        _require(self.behavior, 'rest_seconds', list, f'{self.path.name}.behavior')
        _require(self.behavior, 'relocation', dict, f'{self.path.name}.behavior')
        idle = _require(self.behavior, 'idle', dict, f'{self.path.name}.behavior')
        _require(idle, 'ambient', dict, f'{self.path.name}.behavior.idle')
        major = _require(idle, 'major', dict, f'{self.path.name}.behavior.idle')
        negative = _require(idle, 'negative', dict, f'{self.path.name}.behavior.idle')
        _require(major, 'actions', list, f'{self.path.name}.behavior.idle.major')
        if 'persistent' in negative and not isinstance(negative['persistent'], bool):
            raise ProfileError(f'{self.path.name}.behavior.idle.negative.persistent must be boolean')
        if negative.get('persistent') is False:
            hold = negative.get('hold_seconds')
            if (not isinstance(hold, list) or len(hold) != 2 or
                    not all(isinstance(v, (int, float)) and v > 0 for v in hold)):
                raise ProfileError(
                    f'{self.path.name}.behavior.idle.negative.hold_seconds needs two positive numbers')
        _require(self.behavior, 'petting', dict, f'{self.path.name}.behavior')
        poke = self.behavior.get('poke')
        if poke is not None:
            if not isinstance(poke, dict):
                raise ProfileError(f'{self.path.name}.behavior.poke must be an object')
            for key in ('max_click_seconds', 'drag_threshold_pixels'):
                if not isinstance(poke.get(key), (int, float)) or poke[key] <= 0:
                    raise ProfileError(f'{self.path.name}.behavior.poke.{key} must be positive')
            region = poke.get('interaction_region')
            if not isinstance(region, str) or region not in self.hit_areas:
                raise ProfileError(
                    f'{self.path.name}.behavior.poke interaction_region must name a hit area')
            levels = _require(poke, 'levels', list, f'{self.path.name}.behavior.poke')
            if not levels:
                raise ProfileError(f'{self.path.name}.behavior.poke.levels must not be empty')
            for expected, level in enumerate(levels, 1):
                if not isinstance(level, dict) or level.get('level') != expected:
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke levels must be sequential from 1')
                if level.get('asset') not in self.expressions:
                    raise ProfileError(
                        f'{self.path.name}: unknown poke expression asset {level.get("asset")}')
                if not isinstance(level.get('persistent_negative', False), bool):
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke persistent_negative must be boolean')
            reconciliation = poke.get('reconciliation', [])
            if not isinstance(reconciliation, list):
                raise ProfileError(
                    f'{self.path.name}.behavior.poke.reconciliation must be a list')
            for index, step in enumerate(reconciliation):
                if not isinstance(step, dict):
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke reconciliation step must be an object')
                if step.get('asset') not in self.expressions:
                    raise ProfileError(
                        f'{self.path.name}: unknown poke reconciliation asset {step.get("asset")}')
                remaining = step.get('remaining_level')
                if (not isinstance(remaining, int) or isinstance(remaining, bool) or
                        not 0 <= remaining < len(levels)):
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke reconciliation remaining_level '
                        'must be an anger level below the maximum')
                if index and remaining > reconciliation[index - 1]['remaining_level']:
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke reconciliation remaining_level '
                        'must not increase')
                values = step.get('parameter_values', {})
                if not isinstance(values, dict):
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke reconciliation parameter_values '
                        'must be an object')
                if any(key not in self.parameter_ids for key in values):
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke reconciliation references '
                        'unknown semantic parameter')
                if 'role' in step and not isinstance(step['role'], str):
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke reconciliation role must be text')
            completion = poke.get('completion')
            if completion is not None:
                if not isinstance(completion, dict):
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke.completion must be an object')
                if completion.get('asset') not in self.expressions:
                    raise ProfileError(
                        f'{self.path.name}: unknown poke completion asset '
                        f'{completion.get("asset")}')
                values = completion.get('parameter_values', {})
                if not isinstance(values, dict) or any(
                        key not in self.parameter_ids for key in values):
                    raise ProfileError(
                        f'{self.path.name}.behavior.poke completion parameter_values '
                        'must use known semantic parameters')

    def parameter_id(self, semantic):
        return self.parameter_ids.get(semantic)

    @staticmethod
    def asset_identifier(item):
        return item.get('source') or item['file']

    def runtime_file(self, asset, kind):
        collection = self.expressions if kind == 'expression' else self.motions
        item = collection.get(asset)
        return item.get('file') if item else None

    def resolve_values(self, semantic_values, available):
        resolved = {}
        missing = []
        for semantic, value in semantic_values.items():
            parameter_id = self.parameter_id(semantic)
            if parameter_id in available:
                resolved[parameter_id] = value
            else:
                missing.append(semantic)
        return resolved, missing

    def expression_index(self, asset):
        for index, item in enumerate(self.assets.get('expressions', [])):
            if self.asset_identifier(item) == asset:
                return index
        return None

    def expression_candidates(self, semantic):
        return self.expression_actions.get(semantic, [])

    def motion_asset(self, semantic):
        item = self.motion_actions.get(semantic)
        return item.get('asset') if isinstance(item, dict) else None

    def behavior_settings(self):
        settings = deepcopy(self.behavior)
        settings['head_rect_model'] = list(self.hit_areas['head']['rect'])
        if 'poke' in settings:
            region = settings['poke']['interaction_region']
            settings['poke']['interaction_rect_model'] = list(self.hit_areas[region]['rect'])
        settings['scale'] = self.presentation['scale']
        settings['window_aspect'] = self.presentation['window_aspect']
        settings['window_height_per_character_height'] = self.presentation['window_height_per_character_height']
        settings['render_window_scale'] = self.presentation.get('render_window_scale', 1.0)
        settings['bottom_offscreen_fraction'] = self.presentation.get(
            'bottom_offscreen_fraction', 0.65)
        settings['idle']['ambient']['expressions'] = deepcopy(self.expression_candidates('ambient'))
        settings['idle']['negative']['expressions'] = deepcopy(self.expression_candidates('negative'))
        settings['petting']['positive_reactions'] = deepcopy(self.expression_candidates('positive'))
        actions = settings['idle']['major'].get('actions', [])
        settings['idle']['major']['actions'] = [
            action for action in actions
            if ((action.get('kind') == 'negative' and self.expression_candidates('negative')) or
                (action.get('kind') == 'sleep' and self.motion_asset('sleep')))
        ]
        return settings


class ProfileRegistry:
    def __init__(self, root, state_path=None):
        self.root = Path(root).resolve()
        self.directory = self.root / 'profiles'
        self.state_path = Path(state_path) if state_path else self.root / 'local' / 'app-state.json'
        self.profiles = {}
        self.errors = {}
        for path in sorted(self.directory.glob('*.json')):
            try:
                profile = ModelProfile(path, self.root)
                if profile.id in self.profiles:
                    raise ProfileError(f'duplicate profile id: {profile.id}')
                self.profiles[profile.id] = profile
            except ProfileError as error:
                self.errors[path.name] = str(error)

    def get(self, profile_id):
        return self.profiles.get(profile_id)

    def find_model(self, model_path):
        target = Path(model_path).resolve()
        return next((profile for profile in self.profiles.values()
                     if profile.model_path == target), None)

    def active_id(self):
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
            profile_id = data.get('active_profile')
            return profile_id if profile_id in self.profiles else None
        except (OSError, ValueError, TypeError):
            return None

    def select(self, requested=None, model_path=None):
        if requested:
            profile = self.get(requested)
            if profile is None:
                raise ProfileError(f'unknown profile: {requested}')
            return profile
        if model_path:
            profile = self.find_model(model_path)
            if profile is None:
                raise ProfileError(f'no profile maps model: {model_path}')
            return profile
        active = self.active_id()
        if active:
            return self.profiles[active]
        if self.profiles:
            return next(iter(self.profiles.values()))
        details = '; '.join(self.errors.values()) or 'profiles directory is empty'
        raise ProfileError(f'no valid model profiles: {details}')

    def set_active(self, profile_id):
        if profile_id not in self.profiles:
            raise ProfileError(f'unknown profile: {profile_id}')
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'active_profile': profile_id}, ensure_ascii=False, indent=2),
                             encoding='utf-8')
        temporary.replace(self.state_path)

    def menu_entries(self):
        return [(profile.id, profile.display_name) for profile in self.profiles.values()]
