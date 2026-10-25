import requests


def classify(value):
    match value:
        case 0:
            return "zero"
        case _:
            return "other"
