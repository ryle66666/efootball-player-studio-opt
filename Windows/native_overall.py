"""V17 native integer overall, independently checked against ARM64 0x82b5bb0.

The weights at 0xcd7170 and the native 147-field mapping are verified against
original engine efd257d9e41b622d6ecb8f51f7b0a8ee8820113fd427c5572025df1c8eecc7b8.
No regression coefficients or saved _overall metadata are read.
"""
PARAMETERS = (47, 14, 18, 17, 19, 21, 22, 20, 27, 28, 23, 15, 24, 25, 37, 34, 38, 35, 36, 39, 16, 32, 29, 30, 31, 40, 33, 26)
WEIGHTS = ((186, 136, 49, 49, 61, 37, 12, 12, 37, 49, 49, 62, 99), (0, 14, 61, 61, 61, 98, 98, 98, 171, 159, 159, 173, 210), (13, 27, 86, 86, 122, 171, 171, 171, 196, 159, 159, 210, 123), (0, 14, 61, 61, 37, 98, 110, 122, 122, 159, 159, 123, 62), (0, 0, 37, 37, 24, 49, 73, 61, 73, 86, 86, 86, 37), (27, 41, 61, 61, 122, 208, 135, 135, 196, 73, 73, 99, 37), (40, 68, 147, 147, 122, 159, 196, 196, 159, 98, 98, 74, 12), (0, 27, 24, 24, 37, 73, 86, 86, 184, 159, 159, 284, 358), (0, 14, 24, 24, 12, 12, 24, 24, 12, 12, 12, 12, 12), (0, 14, 24, 24, 12, 12, 24, 24, 12, 12, 12, 12, 12), (0, 55, 24, 24, 61, 24, 12, 12, 24, 24, 24, 25, 62), (13, 286, 147, 147, 220, 86, 49, 49, 24, 12, 12, 0, 0), (0, 191, 86, 86, 122, 86, 24, 24, 24, 12, 12, 12, 12), (0, 82, 37, 37, 98, 37, 12, 12, 12, 12, 12, 12, 12), (53, 27, 24, 24, 49, 73, 24, 24, 73, 61, 61, 99, 123), (13, 136, 220, 220, 61, 61, 196, 196, 98, 220, 220, 86, 99), (40, 150, 184, 184, 61, 86, 159, 159, 86, 159, 159, 99, 123), (80, 204, 98, 98, 122, 49, 24, 24, 24, 37, 37, 37, 86), (0, 0, 24, 24, 12, 24, 61, 61, 24, 73, 73, 74, 86), (133, 109, 37, 37, 37, 12, 12, 12, 12, 24, 24, 37, 62), (279, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), (226, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), (226, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), (173, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), (173, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), (0, 68, 196, 196, 196, 196, 147, 147, 86, 49, 49, 49, 37), (4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4), (0, 14, 24, 24, 24, 24, 24, 24, 24, 24, 24, 12, 12))


POSITION_COLUMNS = (0, 1, 3, 2, 4, 5, 7, 6, 8, 10, 9, 11, 12)
REVISION = 'android-native-ovr-integer-v2'
MIN_RATING = 40
MAX_RATING = 160  # ceil(95 * max(column sum) / 1000), not a seven-bit field.
ABILITIES = tuple(index for index in range(14,41) if index != 33)
APTITUDE_FACTORS = {
    14:(.8199999928474426,.9399999976158142,1.),
    18:(.800000011920929,.9200000166893005,1.),
    17:(.800000011920929,.9200000166893005,1.),19:(1.,1.,1.),
    21:(.800000011920929,.9200000166893005,1.),
    22:(.800000011920929,.9200000166893005,1.),
    20:(.800000011920929,.9200000166893005,1.),
    27:(.800000011920929,.9200000166893005,1.),
    28:(.800000011920929,.9200000166893005,1.),
    23:(.800000011920929,.9200000166893005,1.),
    15:(.8199999928474426,.9399999976158142,1.),
    24:(.800000011920929,.9200000166893005,1.),25:(1.,1.,1.),
    37:(.8199999928474426,.9399999976158142,1.),
    34:(.800000011920929,.9200000166893005,1.),
    38:(.8199999928474426,.9399999976158142,1.),
    35:(.8399999737739563,.9599999785423279,1.),
    36:(.8399999737739563,.9599999785423279,1.),
    39:(.800000011920929,.9200000166893005,1.),
    16:(.8199999928474426,.9399999976158142,1.),
    32:(.8199999928474426,.9399999976158142,1.),
    29:(.8199999928474426,.9399999976158142,1.),
    30:(.8199999928474426,.9399999976158142,1.),
    31:(.8199999928474426,.9399999976158142,1.),
    40:(.8399999737739563,.9599999785423279,1.),26:(1.,1.,1.)}


def weighted_score(parameters, position=None, boosts=None):
    """Unrounded integer numerator from Android 0x82b5784, for exact planning."""
    if len(parameters) != 147:
        raise ValueError('Native overall requires 147 parameters')
    position = parameters[0] if position is None else position
    if type(position) is not int or not 0 <= position < 13:
        raise ValueError('Invalid native position')
    position = POSITION_COLUMNS[position]
    values = [parameters[index] for index in PARAMETERS]
    if any(type(n) is not int or not 0 <= n <= 255 for n in values):
        raise ValueError('Invalid native rating parameters')
    values[0] -= 111
    values[26] = (values[26] * 59) // 3 + 40
    boosts = boosts or {}
    return sum(weights[position] * max(15, min(95, max(0, value - 25) + boosts.get(index, 0)))
               for weights, index, value in zip(WEIGHTS, PARAMETERS, values))


def rating(parameters, position=None, boosts=None):
    return max(MIN_RATING, (weighted_score(parameters,position,boosts)+500)//1000)


def parameters_for_project(project,catalog,stats=None):
    """Use authored physical/trait fields, never a source player's OVR anchor."""
    identity=project.get('identity')or{}
    source=((project.get('source_player')or{}).get('original_payload')or{}).get('parameters')or[]
    params=list(source[:147])+[0]*max(0,147-len(source))
    params[0]=catalog['POSITIONS'].index(identity.get('position','CF'))
    params[47]=int(identity.get('height_cm',source[47]if len(source)>47 else 180))
    weak=(project.get('traits')or{}).get('weak_foot_accuracy')
    params[33]=['低','普通','高','极高'].index(weak)if weak is not None else(source[33]if len(source)>33 else 1)
    for key,value in (stats if stats is not None else project['base_abilities']).items():
        if int(key) in ABILITIES:params[int(key)]=int(value)
    return params


def lineup_parameters(parameters,proficiency=None,aptitude=2,manager=False):
    """Android's team-display input stage, NOT the AI's conservative estimator.

    Native 0x82b78cc applies per-attribute aptitude then proficiency with f32
    truncation.  That team copy clamps raw attributes to 40..99 before player
    and coach boosters are passed separately to the integer kernel. A card
    without a manager/position conversion uses its original trained inputs.
    Condition arrows are a separate match input; a weekly A-E letter is not one.
    """
    if type(aptitude)is not int or aptitude not in (0,1,2):raise ValueError('Invalid native position aptitude')
    result=list(parameters)
    if not manager and aptitude==2:return result
    from coach_formula import f32,factor
    multiplier=factor(proficiency)
    for index in ABILITIES:
        value=result[index]
        if aptitude<2:
            value+=int(f32(f32(f32(value)*APTITUDE_FACTORS[index][aptitude])-f32(value)))
        if manager:
            value+=int(f32(f32(f32(value)*multiplier)-f32(value)))
        result[index]=max(40,min(99,value))
    return result


def lineup_rating(parameters,position=None,boosts=None,proficiency=None,aptitude=2,manager=False):
    return rating(lineup_parameters(parameters,proficiency,aptitude,manager),position,boosts)


ENGINE_CHECKS = (('weights', 13463920, 1456, '34d08a4df83cc1a8f224f3545df64c5f7f90c8db5d384c553d68e1b4548f4d76'), ('position_columns', 12833252, 52, '4c01face86873062acc196a339ecad0364eea3f22464026fddbabdbaa7daee61'), ('main_to_flat', 13463688, 48, '46cfa66762b058b23547f8ed67e95939ad8ce68d158e0c5ccf069091f304d66b'), ('flat_parameters', 13463788, 112, '946ee86c4a62ff917cad6e9dc079c59739ceddc2bebd543107676645389b4c57'), ('individual_kernel', 137041796, 1264, 'f900a9d6edced38bf2c18c911ee37c375fba99a9a5e4d844860991db18ee82b7'), ('team_kernel', 118104868, 340, '01f1ea3e84ac8ed229e5d4d4bf90c1fd5c84712d7beeb2a9a73a245da100f880'), ('manager_factor', 137052104, 296, '239d89d31ae20fd92ac7d1b8f72e07be483d696b33bfc3d468802140e1dd2aea'))


def verify_engine(stream):
    import hashlib
    for name, offset, size, expected in sorted(ENGINE_CHECKS, key=lambda row: row[1]):
        stream.seek(offset)
        data = stream.read(size)
        if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('原生评分关键逻辑已变化（' + name + '），无法确认 AI 强度门槛，请重新适配该包')
    return {'native_integer_rating_verified': True, 'critical_regions': len(ENGINE_CHECKS),
            'full_engine_fingerprint_required': False, 'actual_client_strength_measured': False}
