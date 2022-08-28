async def app(scope, receive, send):
    while True:
        event = await receive()
        if event['type'] == 'http.disconnect':
            break
        elif event['type'] == 'http.request' and not event.get('more_body', False):
            await send_data(send)
            break
        elif event['type'] == 'lifespan.startup':
            await send({'type': 'lifespan.startup.complete'})
        elif event['type'] == 'lifespan.shutdown':
            await send({'type': 'lifespan.shutdown.complete'})
            break

async def send_data(send):
    await send({
        'type': 'http.response.start',
        'status': 200,
        'headers': [(b'content-length', b'5')],
    })
    await send({
        'type': 'http.response.body',
        'body': b'Hello',
        'more_body': False,
    })
