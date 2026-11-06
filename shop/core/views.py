from django.http import JsonResponse


def health(request):
    """Shallow: the process is up and answering. Touches nothing else."""
    return JsonResponse({"status": "ok"})
