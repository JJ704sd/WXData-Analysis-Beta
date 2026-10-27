"""Single version source for the four supported archive products."""
import json

GROUP_RELEASE = 'v71'
GROUP_VERSION = '1.51.5'
PRIVATE_VERSION = '1.20.3'


def release_matrix():
    return {
        'schemaVersion': 1,
        'desktopRelease': GROUP_RELEASE,
        'products': [
            {'client': client, 'conversationMode': mode,
             'version': PRIVATE_VERSION if mode == 'direct' else GROUP_VERSION}
            for client in ('wechat', 'wecom') for mode in ('group', 'direct')
        ],
    }


if __name__ == '__main__':
    print(json.dumps(release_matrix(), indent=2))
