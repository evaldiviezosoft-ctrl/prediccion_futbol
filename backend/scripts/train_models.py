"""Punto de entrada reservado para el pipeline cronológico de entrenamiento.

La ingesta normalizada está implementada, pero el entrenamiento automático aún
no lo está. La guía vigente se encuentra en ``docs/API_FOOTBALL_IMPORT.md``.
"""


def main() -> None:
    print('Los cinco bundles europeos existentes están en backend/models/.')
    print('El entrenamiento sudamericano todavía no está implementado.')
    print('Consulta docs/API_FOOTBALL_IMPORT.md antes de publicar un bundle nuevo.')


if __name__ == '__main__':
    main()
