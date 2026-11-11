import math
import statistics
from collections import Counter


def describe(records):
    return {'completed': sum(row['event'] == 'complete' for row in records), 'cancelled': sum(row['event'] == 'cancel' for row in records), 'waiting': sum(row['event'] == 'waiting' for row in records)}
