FIELDS = {'observation_id': 'str', 'ticket': 'str', 'channel': 'str', 'duration': 'float', 'event': 'str', 'opened': 'int', 'observed': 'int', 'product': 'str', 'revision': 'int'}
REQUIRED = ['observation_id', 'ticket', 'channel', 'duration', 'event', 'opened', 'observed', 'product', 'revision']
ENTITY = 'ticket'
GROUP = 'channel'
MEASURE = 'duration'
PRODUCT = 'repair-competing-risks'
UNIT = '小时'
