import cloudinary
import cloudinary.uploader

def upload_listing_image(file):
    result = cloudinary.uploader.upload(
        file,
        folder="lac/listings",
        resource_type="image",
    )

    return result["secure_url"]
