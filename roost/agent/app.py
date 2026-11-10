"""HTTP API of the agent. Every route requires the shared bearer token."""

import hmac

from flask import Flask, jsonify, request

from roost.agent.supervisor import AlreadyRunning, StartFailed, Supervisor


def create_app(supervisor: Supervisor, token: str) -> Flask:
    if not token:
        raise ValueError('a bearer token is required')
    app = Flask(__name__)
    expected = f'Bearer {token}'.encode()

    @app.before_request
    def require_token():
        supplied = request.headers.get('Authorization', '').encode()
        if not hmac.compare_digest(supplied, expected):
            return jsonify(error='unauthorized'), 401

    @app.get('/health')
    def health():
        return jsonify(status='ok')

    @app.get('/types')
    def types():
        return jsonify(types={
            name: {'params': unit_type.params}
            for name, unit_type in supervisor.unit_types.items()})

    @app.get('/units')
    def units():
        return jsonify(units=supervisor.units())

    @app.post('/units')
    def spawn():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error='expected a JSON object'), 400
        type_name = body.get('type')
        params = body.get('params', {})
        if not isinstance(type_name, str) or not isinstance(params, dict):
            return jsonify(
                error='type must be a string and params an object'), 400
        if type_name not in supervisor.unit_types:
            return jsonify(error=f'unknown unit type {type_name!r}'), 404
        try:
            unit = supervisor.spawn(type_name, body.get('name'), params)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except AlreadyRunning as exc:
            return jsonify(error=str(exc)), 409
        except StartFailed as exc:
            return jsonify(error=str(exc), log_path=exc.log_path), 500
        return jsonify(unit=unit), 201

    @app.delete('/units/<name>')
    def remove(name):
        if not supervisor.teardown(name):
            return jsonify(error=f'no unit named {name!r}'), 404
        return jsonify(stopped=name)

    return app
