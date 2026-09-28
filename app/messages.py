# All customer-facing and employee-facing text lives here.
# Edit or translate without touching any logic.
# Default language: Spanish.

MESSAGES = {
    # Employee-facing messages
    "image_received": "📷 ¡Imagen recibida! Extrayendo datos, esto puede tardar un minuto...",
    "download_failed": "❌ No pude descargar la imagen de WhatsApp. Por favor, intenta enviarla de nuevo.",
    "duplicate_found": "⚠️ Esta inspección ya fue registrada.\nEstado actual: {status}",
    "extraction_complete": (
        "✅ *Datos Extraídos*\n\n"
        "Nombre: {customer_name}\n"
        "Teléfono: {customer_phone}\n"
        "Servicio: {service_description}\n"
        "Fecha: {date}\n\n"
        "Responde *OK* para confirmar y programar la encuesta, "
        "o envía una corrección (ej: 'El nombre es Juan Pérez')."
    ),
    "confirmed": "✅ ¡Confirmado! La encuesta de satisfacción ha sido programada.",
    "correction_processing": "🔄 Procesando corrección...",
    "correction_applied": (
        "🔄 *Datos Actualizados*\n\n"
        "Nombre: {customer_name}\n"
        "Teléfono: {customer_phone}\n"
        "Servicio: {service_description}\n"
        "Fecha: {date}\n\n"
        "Responde *OK* para confirmar, o envía otra corrección."
    ),
    "no_pending": "No tengo inspecciones pendientes de tu confirmación en este momento.",
    "unauthorized": "Lo siento, no estás autorizado para usar este servicio.",

    # Customer-facing messages
    # NOTE: The first message to the customer MUST use a pre-approved WhatsApp template.
    # The template name and its parameters are configured below.
    "survey_template_name": "satisfaction_survey",
    "survey_template_language": "es",
    # Template body example (register this exact text in Meta Business Manager):
    # "Hola {{1}}, gracias por utilizar nuestros servicios de {{2}}.
    #  Nos encantaría conocer tu opinión. ¿Cómo calificarías tu experiencia?"
    # With quick reply buttons: Bueno / Regular / Malo

    "survey_followup": "Gracias por tu respuesta. ¿Hay algo más que quieras comentarnos? (Puedes responder con texto libre o ignorar este mensaje)",
    "survey_thanks": "¡Muchas gracias por tu retroalimentación! Que tengas un excelente día. 😊",
    "opt_out_confirmed": "Has sido removido de nuestra lista de contacto. No recibirás más mensajes de nuestra parte.",

    # Opt-out keywords (case-insensitive)
    "opt_out_keywords": ["stop", "para", "parar", "detener", "no más", "cancelar", "basta"],
}
