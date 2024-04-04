Flow
====

These are the expected event flows/sequences.

H11/H2
------

A typical HTTP/1 or HTTP/2 request with response with the connection
specified to close on response.

.. mermaid::

   sequenceDiagram
     TCPServer->>H11/H2: RawData
     H11/H2->>HTTPStream: Request
     H11/H2->>HTTPStream: Body
     HTTPStream->>App:  http.request[more_body=True]
     H11/H2->>HTTPStream: EndBody
     HTTPStream->>App:  http.request[more_body=False]
     App->>HTTPStream: http.response.start
     App->>HTTPStream: http.response.body
     HTTPStream->>H11/H2: Response
     H11/H2->>TCPServer: RawData
     HTTPStream->>H11/H2: Body
     H11/H2->>TCPServer: RawData
     HTTPStream->>H11/H2: EndBody
     H11/H2->>TCPServer: RawData
     H11/H2->>HTTPStream: StreamClosed
     HTTPStream->>App: http.disconnect
     H11/H2->>TCPServer: Closed


H11 early client cancel
-----------------------

The flow as expected if the connection is closed before the server has
the opportunity to respond.

.. mermaid::

   sequenceDiagram
     TCPServer->>H11/H2: RawData
     H11/H2->>HTTPStream: Request
     H11/H2->>HTTPStream: Body
     HTTPStream->>App:  http.request[more_body=True]
     TCPServer->>H11/H2: Closed
     H11/H2->>HTTPStream: StreamClosed
     HTTPStream->>App: http.disconnect
