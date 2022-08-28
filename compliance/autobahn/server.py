async def app(scope, receive, send):
    while True:
        event = await receive()
        if event['type'] == 'websocket.disconnect':
            break
        elif event['type'] == 'websocket.connect':
            await send({'type': 'websocket.accept'})
        elif event['type'] == 'websocket.receive':
            await send({
                'type': 'websocket.send',
                'bytes': event['bytes'],
                'text': event['text'],
            })
        elif event['type'] == 'lifespan.startup':
            await send({'type': 'lifespan.startup.complete'})
        elif event['type'] == 'lifespan.shutdown':
            await send({'type': 'lifespan.shutdown.complete'})
            break
