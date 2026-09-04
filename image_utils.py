
import cloudinary.uploader


# =========================================================
# IMAGE VALIDATION
# =========================================================

def allowed_image_file(filename):

    allowed_extensions = {
        "png",
        "jpg",
        "jpeg",
        "webp",
    }

    if "." not in filename:
        return False

    extension = (
        filename
        .rsplit(".", 1)[1]
        .lower()
    )

    return extension in allowed_extensions


# =========================================================
# CLOUDINARY UPLOAD
# =========================================================

def upload_lac_image(
    image_file,
    folder="lac/submissions",
):

    if (
        not image_file
        or not image_file.filename
    ):
        return None


    if not allowed_image_file(
        image_file.filename
    ):
        raise ValueError(
            "Only PNG, JPG, JPEG and WEBP images are allowed."
        )


    result = cloudinary.uploader.upload(

        image_file,

        folder=folder,

        resource_type="image",

    )


    return result[
        "secure_url"
    ]
