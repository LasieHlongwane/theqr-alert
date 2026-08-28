import os
from datetime import datetime

from flask import current_app, url_for
from werkzeug.utils import secure_filename


ALLOWED_IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
}


def allowed_image_file(filename):

    return (
        "."
        in filename
        and filename.rsplit(
            ".",
            1,
        )[1].lower()
        in ALLOWED_IMAGE_EXTENSIONS
    )


def save_uploaded_image(file):

    if (
        not file
        or not file.filename
    ):
        return None

    if not allowed_image_file(
        file.filename
    ):
        raise ValueError(
            "Only PNG, JPG, JPEG and WEBP images are allowed."
        )

    upload_folder = os.path.join(
        current_app.root_path,
        "static",
        "uploads",
    )

    os.makedirs(
        upload_folder,
        exist_ok=True,
    )

    original_name = (
        secure_filename(
            file.filename
        )
    )

    timestamp = datetime.utcnow().strftime(
        "%Y%m%d%H%M%S%f"
    )

    filename = (
        f"{timestamp}_{original_name}"
    )

    full_path = os.path.join(
        upload_folder,
        filename,
    )

    file.save(
        full_path
    )

    return url_for(
        "static",
        filename=f"uploads/{filename}",
    )